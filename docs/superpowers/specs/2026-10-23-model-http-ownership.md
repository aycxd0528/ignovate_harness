# 模型HTTP原生连接所有权与失败关闭

原始goal完整保持active：review全项目遗漏并修复，使复杂长任务稳定交付。上一轮phase21已验收2111/48skip、安装656/5skip157Python43paths、JS17，wheel SHA6b85f714b933ab73bb7f3d401950d0dbe8c182a3752667daac263833e8e4fd69。继续已授权native inline/offline/noAPI/no commitpush，保留分支IDE/CSS。

## 原生证据与要求

真实AgentRuntimeFactory/ChatDeepSeek/OpenAI3.26.0/httpx2/httpcore2 2.13.1，经仅127.0.0.1有限时HTTP1.1服务建立2条真实keepalive TCP。连接close内故障使SDK标closed并清pool，Factory首次保留client但再次close清owner，两个真实socket均仍open；全局asyncio.run取消在第一连接实际close后使第二socket遗留。日志 /tmp/ignovate-http-close-{failure,global}-discovery.log。macOS系统HTTP代理最初将keepalive改close导致探针未准入；专属子进程NO_PROXY=*后实际建立2连接，不改产品/用户代理配置。

1. 在SDK可移除连接之前拥有实际native stream；SDK close/is_closed或空pool不能作为socket关闭证明。失败保留原资源，重试必须真正close或继续明确失败。
2. 已接纳Factory/私有child/model切换后的所有sync/async客户端均覆盖；单项失败不跳其他资源。共享旧图既有所有权规则保持。
3. 正常SDK请求/read/write/connect/TLS/retry/timeout沿用SDK，同loop/默认代理/证书/limits保持；不得将SQLite不可取消helper扩用任意SDK调用。
4. 默认HTTP backend连接返回时包装实际stream，正常关闭及时撤销记录；池移除失败资源仍持有。TLS升级更新同一个entry，不积累全部已关闭历史。
5. HTTP native最终收尾专用Task防全局cancel，仅执行已拥有stream关闭和等待已开始connect，不包SDK request或其timeout；caller取消仍传播。永久卡死native close/connect不承诺有界退出。
6. 关闭封新连接准入，等待已开始connect交接后收齐。正常close与失败后retry都检查实际native socket已关闭，非GC/进程退出。

## 设计与取舍

仅snapshot pool于close前无法覆盖SDK请求中已移除的失败连接；只retry SDK.close会noop；改全局SDK/替换全部HTTP协议或逐请求子进程成本过大。选择每个Factory拥有的默认SDK HTTP客户端的pool backend装饰器，保留原backend与协议全部实现，仅接管成功返回的native stream生命周期。

新增core/model_http.py封装sync/async backend与stream、关闭owner、专用native settlement Task；runtime_resources记录实际SDKclient到owner映射，统一全部资源关闭入口。默认httpx/httpx2传输通过实例pool backend安装，非全局monkeypatch。SDK client/transport custom未知形态沿用原close契约，不宣称其native保证；当前产品默认backend须有真实测试。接口耦合及未来升级需明确版本/原生门，不扩大为通用SDK支持承诺。

## 验证

真实子进程服务父临时目录托管、NO_PROXY仅探针env，native socket保留至facts后再有限进程退出。先RED：失败close重试、全局cancel；再normal/caller/repeatedcancel/timeout、native stream fault、多client/retired/shared/child、TLS及legacy httpx、sync、FD/worker基线、connect-close交错。SDK超时必须仍触发。最终一次fresh Astra精确phase21 wheel增量，作者唯一C/I RED/GREEN pass，无二次review；Python/JS/diff、wheel真实安装和来源字节、CLI smoke。所有Ruling和declined逐项裁决并持久化后仅删本轮scratch。
