"""Retained real TCP descriptors must close even after SDK state is lost."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nailong.core import runtime_resources


class ModelHTTPOwnershipTests(unittest.TestCase):
    def probe(self,mode):
        with tempfile.TemporaryDirectory(prefix='model HTTP 原生 ') as temporary:
            root=Path(runtime_resources.__file__).resolve().parents[2]
            environment={**os.environ,'PYTHONPATH':str(root),'NO_PROXY':'*','no_proxy':'*'}
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).parent/'fixtures/model_http_probe.py'),temporary,mode],
                capture_output=True,text=True,env=environment,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stderr,'')
            facts=json.loads(result.stdout.splitlines()[-1])
            expected=1 if mode.startswith('sync-') or mode in ('connecting-close','direct-sdk-connecting-close','retirement-failure','tls-untrusted','tls-handshake-timeout') else 2
            if mode in ('churn','sdk-timeout'):expected=facts['sockets']
            self.assertEqual(facts['sockets'],expected,facts)
            self.assertTrue(facts['sdk_closed'],facts)
            self.assertEqual(facts['native_open_after_loop'],[False]*expected,facts)
            self.assertEqual(facts['owned_after_retry'],0,facts)
            self.assertEqual(facts['fd_after'],facts['fd_before'],facts)
            self.assertEqual(facts['new_threads'],[],facts)
            self.assertFalse(any('Event loop is closed' in error for error in facts['loop_errors']),facts)
            return facts

    def test_normal_default_sdk_closes_both_retained_native_sockets(self):
        self.probe('normal')

    def test_real_sdk_close_failure_cannot_make_retry_drop_open_sockets(self):
        self.assertEqual(self.probe('failure')['first_error'],'OSError')

    def test_global_shutdown_closes_second_socket_after_first_connection_is_cancelled(self):
        self.assertTrue(self.probe('global')['caller_cancelled'])

    def test_failure_inside_actual_native_stream_keeps_original_socket_retryable(self):
        facts=self.probe('native-failure')
        self.assertTrue(any(facts['native_open_after_failure']),facts)
        self.assertEqual(facts['owned_after_failure'],1,facts)
        self.assertGreaterEqual(facts['close_attempts'],3,facts)

    def test_global_shutdown_during_native_settlement_still_closes_all_sockets(self):
        self.assertTrue(self.probe('native-global')['caller_cancelled'])

    def test_single_caller_cancel_still_joins_real_http_cleanup(self):
        self.assertTrue(self.probe('caller-cancel')['caller_cancelled'])

    def test_repeated_caller_cancel_still_joins_real_http_cleanup(self):
        self.assertTrue(self.probe('repeated-cancel')['caller_cancelled'])

    def test_legacy_httpx_native_failure_retries_real_socket(self):
        self.assertGreaterEqual(self.probe('legacy-native-failure')['close_attempts'],3)

    def test_legacy_httpx_global_shutdown_closes_real_sockets(self):
        self.probe('legacy-global')

    def test_tls_upgrade_closes_actual_underlying_sockets(self):
        self.probe('tls-normal')

    def test_tls_native_failure_still_retries_original_connection(self):
        self.assertGreaterEqual(self.probe('tls-native-failure')['close_attempts'],3)

    def test_sync_default_sdk_native_sockets_close(self):
        self.probe('sync-normal')

    def test_sync_native_close_failure_is_retryable(self):
        self.assertGreaterEqual(self.probe('sync-native-failure')['close_attempts'],3)

    def test_real_sdk_request_timeout_still_propagates(self):
        self.assertTrue(self.probe('sdk-timeout')['sdk_timeout'])

    def test_normally_retired_stream_receipts_do_not_accumulate(self):
        self.assertEqual(self.probe('churn')['churn'],12)

    def test_close_waits_for_inflight_connect_then_seals_new_connections(self):
        self.assertTrue(self.probe('connecting-close')['sealed'])

    def test_unverified_retirement_closes_admission_before_another_socket_can_accumulate(self):
        self.assertTrue(self.probe('retirement-failure')['sealed'])

    def test_sync_tls_upgrade_closes_real_ssl_socket(self):
        self.probe('sync-tls-normal')

    def test_legacy_sync_client_keeps_default_native_close_contract(self):
        self.probe('sync-legacy-normal')

    def test_sync_tls_upgrade_cannot_publish_a_socket_after_factory_close_returns(self):
        facts=self.probe('sync-tls-close-race')
        self.assertEqual(facts['closed_at_factory_return'],[True])
        self.assertTrue(facts['loop_progressed_during_close'],facts)

    def test_retired_model_keeps_native_connections_owned_until_factory_close(self):
        self.probe('retired')

    def test_private_child_native_cleanup_preserves_parent_model(self):
        self.assertTrue(self.probe('child')['parent_open'])

    def test_failed_tls_verification_closes_retained_native_socket(self):
        self.assertEqual(self.probe('tls-untrusted')['request_error'],'ConnectError')

    def test_real_sdk_tls_handshake_timeout_still_propagates(self):
        self.assertEqual(self.probe('tls-handshake-timeout')['request_error'],'APITimeoutError')

    def test_global_shutdown_joins_sync_tls_executor_handoff_before_loop_returns(self):
        self.assertTrue(self.probe('sync-tls-close-race-global')['caller_cancelled'])

    def test_concurrent_child_release_and_factory_close_share_actual_async_close(self):
        facts=self.probe('concurrent-child')
        self.assertEqual(facts['concurrent_close_errors'],[],facts)
        self.assertEqual(facts['child_close_calls'],1,facts)
        self.assertTrue(facts['sqlite_closed'],facts)
        self.assertEqual(facts['sync_clients_retained'],0,facts)
        self.assertEqual(facts['close_operations_retained'],0,facts)

    def test_concurrent_child_async_close_failure_is_shared_and_retryable(self):
        facts=self.probe('concurrent-child-failure')
        self.assertTrue(facts['shared_failure'],facts)
        self.assertEqual(facts['child_close_calls'],1,facts)
        self.assertTrue(all(facts['native_open_after_failure']),facts)
        self.assertTrue(facts['sqlite_closed'],facts)
        self.assertEqual(facts['sync_clients_retained'],0,facts)
        self.assertEqual(facts['close_operations_retained'],0,facts)

    def test_concurrent_child_release_and_factory_close_share_sync_executor_future(self):
        facts=self.probe('sync-concurrent-child')
        self.assertEqual(facts['concurrent_close_errors'],[],facts)
        self.assertEqual(facts['child_close_calls'],1,facts)
        self.assertTrue(facts['sqlite_closed'],facts)
        self.assertEqual(facts['sync_clients_retained'],0,facts)
        self.assertEqual(facts['close_operations_retained'],0,facts)

    def test_concurrent_child_sync_close_failure_is_shared_and_retryable(self):
        facts=self.probe('sync-concurrent-child-failure')
        self.assertTrue(facts['shared_failure'],facts)
        self.assertEqual(facts['child_close_calls'],1,facts)
        self.assertTrue(all(facts['native_open_after_failure']),facts)
        self.assertTrue(facts['sqlite_closed'],facts)
        self.assertEqual(facts['sync_clients_retained'],1,facts)
        self.assertEqual(facts['close_operations_retained'],0,facts)

    def assert_direct_sdk_guard(self,mode):
        facts=self.probe(mode)
        self.assertEqual(facts['sync_close_error'],'RuntimeError:Async runtime resources require await factory.aclose().',facts)
        self.assertFalse(facts['factory_marked_closing'],facts)
        self.assertTrue(facts['sqlite_still_open'],facts)

    def test_sync_close_rejects_direct_async_sdk_use_before_destroying_sqlite(self):
        self.assert_direct_sdk_guard('direct-sdk-close')

    def test_sync_close_rejects_inflight_async_sdk_connect_before_socket_handoff(self):
        self.assert_direct_sdk_guard('direct-sdk-connecting-close')
