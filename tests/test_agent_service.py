import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, ToolMessage
from langgraph.types import Command

from agent_service import AgentService, TurnRecursionLimitError
from nailong.core.permissions import ApprovalDecision, PermissionEngine
from nailong.core.sessions import ProjectSessionStore
import local_tools


def graph_output(answer, actions=()):
    interrupts = ()
    if actions:
        interrupts = (
            SimpleNamespace(
                value={"action_requests": list(actions)},
            ),
        )
    return SimpleNamespace(
        value={"messages": [AIMessage(content=answer)]},
        interrupts=interrupts,
    )


class FakeAsyncAgent:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.step = 0

    async def ainvoke(self, value, config, version):
        self.calls.append((value, config, version))
        self.step += 2
        return next(self.outputs)

    def get_state(self, config):
        return SimpleNamespace(metadata={"step": self.step})


class FakeStreamingAgent(FakeAsyncAgent):
    def __init__(self, streams):
        self.streams = iter(streams)
        self.calls = []
        self.step = 0

    async def astream(self, value, config, *, stream_mode):
        self.calls.append((value, config, stream_mode))
        self.step += 2
        for item in next(self.streams):
            yield item


class FakeRewindAgent:
    def __init__(self, messages):
        self.messages = list(messages)
        self.updates = []

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": list(self.messages)})

    async def aupdate_state(self, config, update):
        self.updates.append((config, update))
        removed = {item.id for item in update["messages"] if isinstance(item, RemoveMessage)}
        self.messages = [message for message in self.messages if message.id not in removed]


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_commentary_is_complete_before_tool_start(self):
        key = "a-long-secret-key-123456789"
        commentary = "I will inspect the file before answering."
        answer = "The file is correct."
        call = {"id": "read-1", "name": "read_file", "args": {"path": "a.py"}}
        agent = FakeStreamingAgent([[
            ("messages", (AIMessageChunk(content=commentary), {})),
            ("updates", {"model": {"messages": [AIMessage(content=commentary, tool_calls=[call])]}}),
            ("updates", {"tools": {"messages": [ToolMessage(content='{"ok": true}', name="read_file", tool_call_id="read-1")]}}),
            ("messages", (AIMessageChunk(content=answer + " " + key[:10]), {})),
            ("messages", (AIMessageChunk(content=key[10:] + " Done."), {})),
            ("updates", {"model": {"messages": [AIMessage(content=answer + " " + key + " Done.")]}}),
        ]])
        service = AgentService(lambda **kwargs: agent, api_key=key)
        events = [event async for event in service.stream_turn(
            "inspect", {"configurable": {"thread_id": "commentary-order"}},
        )]
        start = next(index for index, event in enumerate(events) if event.kind == "tool_start")
        end = next(index for index, event in enumerate(events) if event.kind == "tool_end")
        self.assertEqual("".join(event.data["text"] for event in events[:start] if event.kind == "token"), commentary)
        self.assertEqual("".join(event.data["text"] for event in events[end + 1:] if event.kind == "token"), answer + " [密钥已隐藏] Done.")
        self.assertNotIn(key, str(events))

    async def test_interrupt_stream_is_closed_before_resuming_same_thread(self):
        request = {"name": "write_file", "args": {"path": "draft.txt", "content": "x"}, "id": "write-1"}
        interrupt = SimpleNamespace(value={"action_requests": [request]})

        class CloseAwareAgent(FakeStreamingAgent):
            def __init__(self):
                super().__init__([])
                self.first_stream_closed = False
                self.resumed_after_close = None

            async def astream(self, value, config, *, stream_mode):
                self.calls.append((value, config, stream_mode))
                self.step += 2
                if len(self.calls) == 1:
                    try:
                        yield ("updates", {"__interrupt__": (interrupt,)})
                    finally:
                        self.first_stream_closed = True
                else:
                    self.resumed_after_close = self.first_stream_closed
                    yield ("updates", {"model": {"messages": [AIMessage(content="已拒绝。")]}})

        agent = CloseAwareAgent()
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(lambda **kwargs: agent, permission_engine=PermissionEngine(directory))
            events = [
                event async for event in service.stream_turn(
                    "写草稿", {"configurable": {"thread_id": "close-before-resume"}},
                    approval_handler=lambda *_: "reject",
                )
            ]

        self.assertTrue(agent.resumed_after_close)
        self.assertEqual(events[-1].data["text"], "已拒绝。")

    async def test_missing_approval_handler_rejects_mutating_action(self):
        request = {
            "name": "write_file",
            "args": {"path": "should-not-exist.txt", "content": "no"},
            "id": "headless-write",
        }
        interrupt = SimpleNamespace(value={"action_requests": [request]})
        agent = FakeStreamingAgent([
            [
                ("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[request])]}}),
                ("updates", {"__interrupt__": (interrupt,)}),
            ],
            [("updates", {"model": {"messages": [AIMessage(content="写入已拒绝。")]}})],
        ])
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                lambda **kwargs: agent,
                permission_engine=PermissionEngine(directory),
            )
            events = [
                event
                async for event in service.stream_turn(
                    "写入文件",
                    {"configurable": {"thread_id": "headless-reject"}},
                )
            ]

        self.assertIn("approval_needed", [event.kind for event in events])
        self.assertIsInstance(agent.calls[1][0], Command)
        self.assertEqual(
            agent.calls[1][0].resume,
            {"decisions": [{"type": "reject"}]},
        )

    async def test_stream_turn_accepts_a_smaller_graph_step_limit(self):
        agent = FakeAsyncAgent([graph_output("完成。")])
        service = AgentService(lambda **kwargs: agent)

        events = [
            event
            async for event in service.stream_turn(
                "只读检查",
                {"configurable": {"thread_id": "bounded-turn"}},
                max_graph_steps=6,
            )
        ]

        self.assertEqual(events[-1].kind, "final")
        self.assertEqual(agent.calls[0][1]["recursion_limit"], 5)

    async def test_stream_turn_emits_usage_metadata_for_the_toolbar(self):
        chunk = AIMessageChunk(
            content="回答",
            usage_metadata={
                "input_tokens": 12,
                "output_tokens": 3,
                "total_tokens": 15,
                "input_token_details": {"cache_read": 5},
            },
        )
        agent = FakeStreamingAgent([[
            ("messages", (chunk, {})),
            ("updates", {"model": {"messages": [AIMessage(content="回答")]}}),
        ]])
        service = AgentService(lambda **kwargs: agent)

        events = [
            event
            async for event in service.stream_turn(
                "问候",
                {"configurable": {"thread_id": "usage-test"}, "recursion_limit": 40},
            )
        ]

        usage = [event.data for event in events if event.kind == "usage"]
        self.assertEqual(
            usage,
            [{"input_tokens": 12, "output_tokens": 3, "cache_hit_tokens": 5, "total_tokens": 15}],
        )

    async def test_stream_turn_emits_each_model_step_usage_before_its_tools(self):
        def usage(input_tokens, output_tokens, cache_hit_tokens):
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "input_token_details": {"cache_read": cache_hit_tokens},
            }

        first = {"name": "read_file", "args": {"path": "one.py"}, "id": "read-one"}
        second = {"name": "read_file", "args": {"path": "two.py"}, "id": "read-two"}
        agent = FakeStreamingAgent([[
            ("messages", (AIMessageChunk(content="", usage_metadata=usage(10, 2, 3)), {})),
            ("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[first], usage_metadata=usage(10, 2, 3))]}}),
            ("updates", {"tools": {"messages": [ToolMessage(content='{"ok": true}', tool_call_id="read-one", name="read_file")]}}),
            ("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[second], usage_metadata=usage(20, 4, 5))]}}),
            ("updates", {"tools": {"messages": [ToolMessage(content='{"ok": true}', tool_call_id="read-two", name="read_file")]}}),
            ("messages", (AIMessageChunk(content="完成", usage_metadata=usage(30, 6, 7)), {})),
        ]])
        service = AgentService(lambda **kwargs: agent)

        events = [event async for event in service.stream_turn(
            "读两个文件", {"configurable": {"thread_id": "multi-step-usage"}},
        )]

        self.assertEqual(
            [event.data for event in events if event.kind == "usage"],
            [
                {"input_tokens": 10, "output_tokens": 2, "cache_hit_tokens": 3, "total_tokens": 12},
                {"input_tokens": 20, "output_tokens": 4, "cache_hit_tokens": 5, "total_tokens": 24},
                {"input_tokens": 30, "output_tokens": 6, "cache_hit_tokens": 7, "total_tokens": 36},
            ],
        )
        kinds = [event.kind for event in events]
        self.assertLess(kinds.index("usage"), kinds.index("tool_start"))
        self.assertEqual(kinds[-2:], ["usage", "final"])

    async def test_rewind_restores_previous_turn_boundary_without_claiming_side_effect_undo(self):
        prior_user = HumanMessage(content="第一轮", id="prior-user")
        prior_answer = AIMessage(content="第一轮答复", id="prior-answer")
        current_user = HumanMessage(content="第二轮", id="current-user")
        current_answer = AIMessage(content="第二轮答复", id="current-answer")
        agent = FakeRewindAgent([prior_user, prior_answer, current_user, current_answer])

        class Factory:
            settings = SimpleNamespace(project_root=Path(tempfile.gettempdir()))

            async def async_runtime(self, **kwargs):
                return agent

        with tempfile.TemporaryDirectory() as directory:
            store = ProjectSessionStore(directory, base_dir=Path(directory) / "state")
            store.append_event("rewind-test", "turn_start", {"message_ids": ["prior-user", "prior-answer"]})
            store.append_event("rewind-test", "final", {"text": "第二轮答复"})
            service = AgentService(
                Factory(),
                session_store=store,
                permission_engine=PermissionEngine(directory),
            )

            removed_count = await service.rewind("rewind-test")
            events = store.read_events("rewind-test")

        self.assertEqual(removed_count, 2)
        self.assertEqual([message.id for message in agent.messages], ["prior-user", "prior-answer"])
        self.assertEqual(
            [message.id for message in agent.updates[0][1]["messages"]],
            ["current-user", "current-answer"],
        )
        self.assertEqual([event["kind"] for event in events], ["rewind"])

    async def test_stream_turn_emits_tokens_and_final_answer_in_order(self):
        stream = [
            ("messages", (AIMessageChunk(content="你好，"), {})),
            ("messages", (AIMessageChunk(content="世界。"), {})),
            ("updates", {"model": {"messages": [AIMessage(content="你好，世界。")]} }),
        ]
        agent = FakeStreamingAgent([stream])
        service = AgentService(lambda **kwargs: agent)

        events = [
            event
            async for event in service.stream_turn(
                "打招呼",
                {"configurable": {"thread_id": "stream-turn"}, "recursion_limit": 40},
            )
        ]

        self.assertEqual(
            [(event.kind, event.data.get("text")) for event in events],
            [("status", None), ("token", "你好，"), ("token", "世界。"), ("final", "你好，世界。")],
        )

    async def test_stream_turn_resumes_after_approval_and_logs_no_raw_tool_args(self):
        request = {
            "name": "write_file",
            "args": {"path": "draft.txt", "content": "private-body"},
            "id": "write-stream",
        }
        interrupt = SimpleNamespace(value={"action_requests": [request]})
        streams = [
            [
                ("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[request])]}}),
                ("updates", {"__interrupt__": (interrupt,)}),
            ],
            [
                ("messages", (AIMessageChunk(content="已写入。"), {})),
                ("updates", {"model": {"messages": [AIMessage(content="已写入。")]}}),
            ],
        ]
        agent = FakeStreamingAgent(streams)
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectSessionStore(directory, base_dir=Path(directory) / "state")
            service = AgentService(
                lambda **kwargs: agent,
                permission_engine=PermissionEngine(directory),
                session_store=store,
            )
            approvals = []

            async def approve(action, index, total):
                approvals.append(action)
                return "approve"

            events = [
                event
                async for event in service.stream_turn(
                    "写入草稿",
                    {"configurable": {"thread_id": "stream-approval"}, "recursion_limit": 40},
                    approval_handler=approve,
                )
            ]
            log_text = store.session_path("stream-approval").read_text(encoding="utf-8")

        self.assertIn("approval_needed", [event.kind for event in events])
        self.assertEqual(events[-1].data["text"], "已写入。")
        self.assertEqual(len(agent.calls), 2)
        self.assertIsInstance(agent.calls[1][0], Command)
        self.assertEqual(agent.calls[1][0].resume, {"decisions": [{"type": "approve"}]})
        self.assertEqual(approvals[0]["args"]["content"], "private-body")
        self.assertIn("private-body", log_text)  # It is only present inside the rendered diff preview.
        self.assertNotIn('"args"', log_text)

    async def test_streaming_redactor_handles_api_key_split_between_model_chunks(self):
        agent = FakeStreamingAgent([
            [
                ("messages", (AIMessageChunk(content="secret-se"), {})),
                ("messages", (AIMessageChunk(content="ntinel 已处理"), {})),
                ("updates", {"model": {"messages": [AIMessage(content="secret-sentinel 已处理")]}}),
            ]
        ])
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectSessionStore(directory, base_dir=Path(directory) / "state", api_key="secret-sentinel")
            service = AgentService(
                lambda **kwargs: agent,
                api_key="secret-sentinel",
                permission_engine=PermissionEngine(directory),
                session_store=store,
            )
            events = [
                event
                async for event in service.stream_turn(
                    "检查输出",
                    {"configurable": {"thread_id": "stream-redact"}, "recursion_limit": 40},
                )
            ]
            log_text = store.session_path("stream-redact").read_text(encoding="utf-8")

        emitted = "".join(event.data["text"] for event in events if event.kind == "token")
        self.assertNotIn("secret-sentinel", emitted)
        self.assertNotIn("secret-sentinel", log_text)
        self.assertEqual(events[-1].data["text"], "[密钥已隐藏] 已处理")

    async def test_approval_resumes_same_thread_and_returns_final_answer(self):
        request = {
            "name": "write_file",
            "args": {"path": "notes.txt", "content": "draft"},
            "id": "write-1",
        }
        agent = FakeAsyncAgent(
            [graph_output("", [request]), graph_output("文件已创建。")]
        )
        profiles = []
        service = AgentService(
            lambda **kwargs: (profiles.append(kwargs) or agent),
            api_key="test-key",
        )
        decisions = []

        async def approve(action, index, total):
            decisions.append((action["name"], index, total))
            return "approve"

        config = {"configurable": {"thread_id": "same-thread"}, "recursion_limit": 40}
        answer = await service.run_turn(
            "创建笔记", config, approval_handler=approve
        )

        self.assertEqual(answer, "文件已创建。")
        self.assertEqual(decisions, [("write_file", 1, 1)])
        self.assertEqual(len(agent.calls), 2)
        self.assertTrue(all(call[1]["configurable"]["thread_id"] == "same-thread" for call in agent.calls))
        self.assertIsInstance(agent.calls[0][0]["messages"][0], HumanMessage)
        self.assertIsInstance(agent.calls[1][0], Command)
        self.assertEqual(
            agent.calls[1][0].resume,
            {"decisions": [{"type": "approve"}]},
        )
        self.assertEqual(
            profiles,
            [{
                "profile": "chat",
                "target_path": None,
                "thread_id": "same-thread",
                "allowed_tools": None,
            }],
        )

    async def test_configured_api_key_in_user_message_is_redacted_before_model_input(self):
        agent = FakeAsyncAgent([graph_output("已处理。")])
        service = AgentService(lambda **kwargs: agent, api_key="secret-sentinel")

        await service.run_turn(
            "误贴 secret-sentinel 请忽略",
            {"configurable": {"thread_id": "redact-user-input"}, "recursion_limit": 40},
        )

        message = agent.calls[0][0]["messages"][0]
        self.assertEqual(message.content, "误贴 [密钥已隐藏] 请忽略")
        self.assertNotIn("secret-sentinel", message.content)

    async def test_hard_denied_path_never_reaches_approval_handler(self):
        request = {
            "name": "write_file",
            "args": {"path": ".env", "content": "overwrite"},
            "id": "write-secret",
        }
        agent = FakeAsyncAgent(
            [graph_output("", [request]), graph_output("已拒绝。")]
        )
        with tempfile.TemporaryDirectory() as directory:
            engine = PermissionEngine(directory, rules={"allow": ["Write(*)"]})
            service = AgentService(lambda **kwargs: agent, permission_engine=engine)
            async def unexpected_approval(*args):
                self.fail("protected paths must not be approvable")

            answer = await service.run_turn(
                "覆盖 .env",
                {"configurable": {"thread_id": "hard-deny"}, "recursion_limit": 40},
                approval_handler=unexpected_approval,
            )

        self.assertEqual(answer, "已拒绝。")
        self.assertEqual(agent.calls[1][0].resume, {"decisions": [{"type": "reject", "message": "受保护路径不能通过文件工具访问。"}]})

    async def test_session_approval_grants_a_scoped_rule_for_following_turns(self):
        request = {
            "name": "run_command",
            "args": {"command": "pytest tests/unit/test_one.py"},
            "id": "run-test",
        }
        agent = FakeAsyncAgent(
            [
                graph_output("", [request]),
                graph_output("第一次完成。"),
                graph_output("", [request]),
                graph_output("第二次完成。"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                lambda **kwargs: agent,
                permission_engine=PermissionEngine(directory),
            )
            approvals = []

            async def approve_session(action, index, total):
                approvals.append(action["_approval"])
                return ApprovalDecision("approve_session")

            config = {"configurable": {"thread_id": "session-rule"}, "recursion_limit": 40}
            first = await service.run_turn("运行测试", config, approval_handler=approve_session)
            second = await service.run_turn("再运行测试", config, approval_handler=approve_session)

        self.assertEqual((first, second), ("第一次完成。", "第二次完成。"))
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["suggested_rule"], "Bash(pytest tests/unit/test_one.py)")
        self.assertEqual(agent.calls[1][0].resume, {"decisions": [{"type": "approve"}]})
        self.assertEqual(agent.calls[3][0].resume, {"decisions": [{"type": "approve"}]})

    async def test_approval_request_contains_reason_and_edit_diff_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "hello.txt").write_text("old line\n", encoding="utf-8")
            request = {
                "name": "edit_file",
                "args": {"path": "hello.txt", "old_string": "old line", "new_string": "new line"},
                "id": "edit-preview",
            }
            agent = FakeAsyncAgent([graph_output("", [request]), graph_output("已修改。")])
            service = AgentService(
                lambda **kwargs: agent,
                permission_engine=PermissionEngine(directory),
            )
            seen = []

            async def reject(action, index, total):
                seen.append(action)
                return "reject"

            await service.run_turn(
                "替换一行",
                {"configurable": {"thread_id": "preview"}, "recursion_limit": 40},
                approval_handler=reject,
            )

        self.assertIn("默认需要你审批", seen[0]["_approval"]["reason"])
        self.assertIn("-old line", seen[0]["_approval"]["preview"]["diff"])
        self.assertIn("+new line", seen[0]["_approval"]["preview"]["diff"])

    async def test_review_profile_auto_rejects_mutating_actions(self):
        request = {
            "name": "run_command",
            "args": {"command": "rm -rf /"},
            "id": "command-1",
        }
        agent = FakeAsyncAgent(
            [graph_output("", [request]), graph_output("审查期间的命令已拒绝。")]
        )
        profiles = []
        service = AgentService(
            lambda **kwargs: (profiles.append(kwargs) or agent)
        )

        async def unexpected_approval(*args):
            self.fail("review mode must never prompt to run a mutation")

        answer = await service.run_turn(
            "审查 src",
            {"configurable": {"thread_id": "review-thread"}, "recursion_limit": 40},
            profile="review",
            target_path=".",
            approval_handler=unexpected_approval,
        )

        self.assertEqual(answer, "审查期间的命令已拒绝。")
        self.assertEqual(profiles[0]["profile"], "review")
        self.assertEqual(
            agent.calls[1][0].resume,
            {"decisions": [{"type": "reject", "message": "审查模式是只读模式。"}]},
        )

    async def test_init_profile_rejects_writes_outside_context_file(self):
        request = {
            "name": "write_file",
            "args": {"path": "README.md", "content": "unexpected"},
            "id": "wrong-write",
        }
        agent = FakeAsyncAgent(
            [graph_output("", [request]), graph_output("写入请求已拒绝。")]
        )
        service = AgentService(lambda **kwargs: agent)

        async def unexpected_approval(*args):
            self.fail("init mode must reject paths outside its one allowed file")

        answer = await service.run_turn(
            "/init",
            {"configurable": {"thread_id": "init-thread"}, "recursion_limit": 40},
            profile="init",
            approval_handler=unexpected_approval,
        )

        self.assertEqual(answer, "写入请求已拒绝。")
        self.assertEqual(
            agent.calls[1][0].resume,
            {"decisions": [{"type": "reject", "message": "初始化模式只能写入 .nailong/context.md。"}]},
        )

    def test_init_profile_rejects_context_symlink_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context_dir = root / ".nailong"
            context_dir.mkdir()
            readme = root / "README.md"
            readme.write_text("keep", encoding="utf-8")
            (context_dir / "context.md").symlink_to(readme)

            with patch("local_tools.PROJECT_ROOT", root):
                service = AgentService(lambda **kwargs: None)
                allowed = service._action_allowed(
                    {
                        "name": "write_file",
                        "args": {
                            "path": ".nailong/context.md",
                            "content": "overwrite",
                        },
                    },
                    "init",
                )

        self.assertFalse(allowed)

    def test_history_redacts_secrets_and_formats_message_roles(self):
        class HistoryGraph:
            def get_state(self, config):
                return SimpleNamespace(
                    values={
                        "messages": [
                            SimpleNamespace(type="human", content="hi"),
                            SimpleNamespace(type="ai", content="key=secret-sentinel"),
                        ]
                    }
                )

        service = AgentService(
            lambda **kwargs: HistoryGraph(),
            api_key="secret-sentinel",
        )

        history = service.get_history("history-thread")

        self.assertEqual(
            history,
            [("user", "hi"), ("assistant", "key=[密钥已隐藏]")],
        )

    async def test_graph_step_budget_covers_all_approval_resumes(self):
        request = {
            "name": "write_file",
            "args": {"path": "notes.txt", "content": "draft"},
            "id": "write",
        }
        agent = FakeAsyncAgent([graph_output("", [request]) for _ in range(45)])
        service = AgentService(lambda **kwargs: agent)
        prompts = []

        async def reject(action, index, total):
            prompts.append(index)
            return "reject"

        with self.assertRaisesRegex(TurnRecursionLimitError, "40 步"):
            await service.run_turn(
                "一直尝试",
                {"configurable": {"thread_id": "budget-thread"}, "recursion_limit": 40},
                max_graph_steps=40,
                approval_handler=reject,
            )

        self.assertLess(len(prompts), 45)
        self.assertLessEqual(agent.step, 40)


class ToolSummaryTests(unittest.IsolatedAsyncioTestCase):
    def test_edit_result_summary_carries_diff_and_redacts_key(self):
        key = "diff-secret-sentinel"
        payload = {"ok": True, "path": "src/a.py", "replacements": 1, "diff": f"-old\n+new {key}"}
        message = ToolMessage(content=json.dumps(payload), name="edit_file", tool_call_id="t1")
        service = AgentService(lambda **kwargs: None, api_key=key)
        summary = service._tool_result_summary(message)
        self.assertEqual(summary["summary"], "替换 1 处")
        self.assertNotIn(key, summary["diff"])
        self.assertIn("[密钥已隐藏]", summary["diff"])

    def test_command_result_summary_previews_tail_output_only(self):
        payload = {"ok": True, "output": "line1\nline2\nline3\nline4\n", "exit_code": 0}
        message = ToolMessage(content=json.dumps(payload), name="run_command", tool_call_id="t1")
        summary = AgentService(lambda **kwargs: None)._tool_result_summary(message)
        self.assertIn("output_snippet", summary)
        self.assertTrue(summary["output_snippet"].startswith("…"))
        self.assertNotIn("line1", summary["output_snippet"])
        self.assertIn("line4", summary["output_snippet"])

    def test_short_command_output_is_visible_in_the_snippet(self):
        payload = {"ok": True, "output": "only line\n", "exit_code": 0}
        message = ToolMessage(content=json.dumps(payload), name="run_command", tool_call_id="t1")
        summary = AgentService(lambda **kwargs: None)._tool_result_summary(message)
        self.assertEqual(summary["output_snippet"], "only line")


if __name__ == "__main__":
    unittest.main()
