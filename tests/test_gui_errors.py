import unittest
from types import SimpleNamespace

import httpx2
import openai

from nailong.gui.errors import describe_run_error


class RunErrorDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.describe = describe_run_error

    def test_empty_timeout_explains_failure_and_names_exception(self):
        text = self.describe(TimeoutError())
        self.assertIn('超时', text)
        self.assertIn('TimeoutError', text)

    def test_empty_non_timeout_keeps_exception_type_visible(self):
        self.assertIn('RuntimeError', self.describe(RuntimeError()))

    def test_preserves_message_and_explicit_transport_cause(self):
        request = httpx2.Request('POST', 'https://example.test/chat')
        error = openai.APIConnectionError(request=request)
        error.__cause__ = ConnectionResetError('peer reset')
        text = self.describe(error)
        for detail in ('APIConnectionError', 'Connection error.', 'ConnectionResetError', 'peer reset'):
            self.assertIn(detail, text)

    def test_cause_chain_is_bounded_and_cycles_terminate(self):
        error = RuntimeError('visible root')
        error.__cause__ = ValueError('visible cause')
        error.__cause__.__cause__ = OSError('visible transport')
        error.__cause__.__cause__.__cause__ = Exception('out of bound')
        text = self.describe(error)
        self.assertIn('visible transport', text)
        self.assertNotIn('out of bound', text)
        error.__cause__.__cause__ = error
        self.assertEqual(self.describe(error).count('visible root'), 1)

    def test_suppressed_context_does_not_reappear(self):
        try:
            try:
                raise ValueError('suppressed detail')
            except ValueError:
                raise RuntimeError('current failure') from None
        except RuntimeError as error:
            text = self.describe(error)
        self.assertIn('current failure', text)
        self.assertNotIn('suppressed detail', text)

    def test_http_error_adds_safe_metadata_without_reading_attached_payloads(self):
        request = httpx2.Request('POST', 'https://example.test/chat',
                                headers={'Authorization': 'Bearer header-secret'},
                                content=b'request-body-secret')
        response = httpx2.Response(503, request=request,
                                  headers={'x-request-id': 'req_test-123', 'x-private': 'response-header-secret'},
                                  content=b'response-body-secret')
        error = openai.APIStatusError('service unavailable', response=response,
                                     body={'private': 'exception-body-secret'})
        text = self.describe(error)
        self.assertIn('HTTP 503', text)
        self.assertIn('req_test-123', text)
        for secret in ('header-secret', 'request-body-secret', 'response-header-secret',
                       'response-body-secret', 'exception-body-secret'):
            self.assertNotIn(secret, text)

    def test_rejects_invalid_or_oversized_metadata(self):
        for status, request_id in ((True, 'bad\nidentifier'), (999, 'x' * 129),
                                   ('503', {'arbitrary': 'payload'})):
            with self.subTest(status=status):
                error = RuntimeError('failure')
                error.status_code = status
                error.request_id = request_id
                text = self.describe(error)
                self.assertNotIn('HTTP', text)
                self.assertNotIn('request_id', text)

    def test_controller_safe_redacts_secrets_in_messages_causes_and_request_id(self):
        from nailong.gui.controller import GUIController
        secret = 'sentinel-secret'
        error = RuntimeError('failed using ' + secret)
        error.request_id = secret
        error.__cause__ = OSError('transport ' + secret)
        controller = GUIController.__new__(GUIController)
        controller.sessions = SimpleNamespace(api_key=secret)
        text = controller.safe(self.describe(error))
        self.assertNotIn(secret, text)
        self.assertIn('[密钥已隐藏]', text)
        self.assertIn('RuntimeError', text)


if __name__ == '__main__':
    unittest.main()
