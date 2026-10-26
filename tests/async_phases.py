"""Wait for a held test phase while retaining an operation's early failure."""
import asyncio


async def wait_for_phase(signal, operation, *, description, timeout=30):
    ready = asyncio.create_task(signal.wait())
    try:
        done, _ = await asyncio.wait(
            {ready, operation}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED)
        if operation in done:
            # Report the real exception instead of replacing it with a timeout.
            await operation
            raise AssertionError(f'Operation finished while waiting for {description}')
        if ready in done:
            return
        raise AssertionError(f'Timed out waiting for {description}: {operation!r}')
    finally:
        if not ready.done():
            ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
