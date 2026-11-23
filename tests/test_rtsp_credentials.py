#!/usr/bin/env python3
"""
Testes de credenciais em URLs RTSP (Issues #18 e #71): senhas com caracteres especiais, IPv6,
sub-stream configurado, mascaramento em texto livre, filtro/formatter global de logging,
diagnose_camera.py e fluxo ponta a ponta no StreamWorker.
"""
import contextlib
import importlib.util
import io
import logging
import os
import sys
import unittest
from unittest import mock
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.app import setup_logging
from central_nvr.core.camera import CameraDevice
from central_nvr.core.log_sanitize import (
    CredentialSanitizingFilter,
    CredentialSanitizingFormatter,
    sanitize_url_credentials,
)
from central_nvr.core.stream_worker import StreamWorker, sanitize_rtsp_url

SPECIAL_PASSWORDS = ["pa/ss", "p@ss", "pa:ss", "pa#ss", "pa?ss", "pa%ss", "a/b@c:d#e?f%g"]


def _cam(password, **kw):
    params = dict(id="c1", name="Cam", ip="192.168.1.10", rtsp_port=554, rtsp_path="/onvif1",
                  username="admin", password=password, manufacturer="Yoosee")
    params.update(kw)
    return CameraDevice(**params)


class SpecialPasswordTest(unittest.TestCase):
    def _assert_url_ok(self, url, password, host="192.168.1.10"):
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.hostname, host, url)
        self.assertEqual(parsed.port, 554, url)
        self.assertEqual(parsed.username, "admin", url)
        self.assertEqual(urllib.parse.unquote(parsed.password), password, url)

    def test_field_built_urls_keep_host(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                cam = _cam(pwd)
                self._assert_url_ok(cam.get_full_rtsp_url(), pwd)
                self._assert_url_ok(cam.get_substream_url(), pwd)
                for url in cam.get_candidate_rtsp_urls(prefer_substream=True):
                    self._assert_url_ok(url, pwd)

    def test_explicit_urls_keep_host(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                cam = _cam(pwd, rtsp_url="rtsp://192.168.1.10:554/main",
                           substream_url="rtsp://192.168.1.10:554/sub")
                self._assert_url_ok(cam.get_full_rtsp_url(), pwd)
                self._assert_url_ok(cam.get_substream_url(), pwd)

    def test_substream_url_receives_credentials(self):
        cam = _cam("s3cret", substream_url="rtsp://192.168.1.10:554/sub")
        self.assertEqual(cam.get_substream_url(), "rtsp://admin:s3cret@192.168.1.10:554/sub")

    def test_ipv6_keeps_brackets(self):
        cam = _cam("s3cret", rtsp_url="rtsp://[fe80::1]:554/live")
        url = cam.get_full_rtsp_url()
        self.assertEqual(url, "rtsp://admin:s3cret@[fe80::1]:554/live")
        self.assertEqual(urllib.parse.urlparse(url).hostname, "fe80::1")

        field_cam = _cam("s3cret", ip="fe80::2")
        self.assertEqual(urllib.parse.urlparse(field_cam.get_full_rtsp_url()).hostname, "fe80::2")

    def test_username_with_special_chars(self):
        cam = _cam("x", username="op@site")
        parsed = urllib.parse.urlparse(cam.get_full_rtsp_url())
        self.assertEqual(urllib.parse.unquote(parsed.username), "op@site")
        self.assertEqual(parsed.hostname, "192.168.1.10")


class SanitizeTest(unittest.TestCase):
    def test_masks_password_in_generated_urls(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                url = _cam(pwd).get_full_rtsp_url()
                sanitized = sanitize_rtsp_url(url)
                self.assertNotIn(urllib.parse.quote(pwd, safe=""), sanitized)
                self.assertIn("admin:****@192.168.1.10:554/onvif1", sanitized)

    def test_masks_raw_unencoded_password(self):
        raw_passwords = [
            *SPECIAL_PASSWORDS,
            "pa ss",
            "minha senha secreta",
            "ab'cd",
            'ab"cd',
            "a b'c\"d@e/f:g",
            "S3nha%2F%40123",
        ]
        for pwd in raw_passwords:
            with self.subTest(pwd=pwd):
                raw = f"rtsp://admin:{pwd}@192.168.1.10:554/onvif1"
                sanitized = sanitize_rtsp_url(raw)
                self.assertNotIn(pwd, sanitized)
                self.assertEqual(sanitized, "rtsp://admin:****@192.168.1.10:554/onvif1")

    def test_url_without_credentials_unchanged(self):
        for url in ("rtsp://192.168.1.50:554/live", "rtsp://admin@192.168.1.50/live", ""):
            self.assertEqual(sanitize_rtsp_url(url), url)

    def test_masks_urls_embedded_in_free_text_and_schemes(self):
        pyav_msg = (
            "ConnectionRefusedError [Errno 111] Connection refused: "
            "'rtsp://admin:S3nha%2F%40123@127.0.0.1:1/x'"
        )
        sanitized = sanitize_url_credentials(pyav_msg)
        self.assertNotIn("S3nha", sanitized)
        self.assertNotIn("%2F%40123", sanitized)
        self.assertEqual(
            sanitized,
            "ConnectionRefusedError [Errno 111] Connection refused: "
            "'rtsp://admin:****@127.0.0.1:1/x'",
        )

        # Senha com aspas simples ou duplas em mensagem de erro não pode truncar antes do `@`
        single_quote_msg = "erro: 'rtsp://admin:ab'cd@10.0.0.1/x'"
        sanitized_sq = sanitize_url_credentials(single_quote_msg)
        self.assertNotIn("ab'cd", sanitized_sq)
        self.assertEqual(sanitized_sq, "erro: 'rtsp://admin:****@10.0.0.1/x'")

        double_quote_msg = 'erro: "rtsp://admin:ab"cd@10.0.0.1/x"'
        sanitized_dq = sanitize_url_credentials(double_quote_msg)
        self.assertNotIn('ab"cd', sanitized_dq)
        self.assertEqual(sanitized_dq, 'erro: "rtsp://admin:****@10.0.0.1/x"')

        # Duas URLs na mesma linha (separadas por espaço ou coladas) são mascaradas individualmente
        two_urls = "'rtsp://a:b@h/x' e 'rtsp://c:d@h2/y'"
        self.assertEqual(
            sanitize_url_credentials(two_urls),
            "'rtsp://a:****@h/x' e 'rtsp://c:****@h2/y'",
        )
        adjacent_urls = "'rtsp://a:b@h/x','rtsp://c:d@h2/y'"
        self.assertEqual(
            sanitize_url_credentials(adjacent_urls),
            "'rtsp://a:****@h/x','rtsp://c:****@h2/y'",
        )

        multi = (
            "Falha em 'http://op:seg%2Fredo@10.0.0.1:80/onvif' e "
            "\"https://user:s3cr%40t@cam.local:443/api\" (contato dev@exemplo.com)"
        )
        sanitized_multi = sanitize_url_credentials(multi)
        self.assertNotIn("seg%2Fredo", sanitized_multi)
        self.assertNotIn("s3cr%40t", sanitized_multi)
        self.assertIn("'http://op:****@10.0.0.1:80/onvif'", sanitized_multi)
        self.assertIn('"https://user:****@cam.local:443/api"', sanitized_multi)
        self.assertIn("dev@exemplo.com", sanitized_multi)


class LoggingSanitizationTest(unittest.TestCase):
    """Testes do filtro e formatter de sanitização com o módulo logging real (Issue #71)."""

    def _make_logger(self, name, *, use_filter=True, use_formatter=True):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        fmt = "%(levelname)s %(name)s: %(message)s"
        handler.setFormatter(
            CredentialSanitizingFormatter(fmt) if use_formatter else logging.Formatter(fmt)
        )
        if use_filter:
            handler.addFilter(CredentialSanitizingFilter())

        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        log.handlers = [handler]
        self.addCleanup(self._cleanup_logger, log)
        return log, stream

    @staticmethod
    def _cleanup_logger(log):
        for h in list(log.handlers):
            log.removeHandler(h)
        log.propagate = True

    def test_filter_masks_msg_args_exceptions_and_traceback(self):
        # Usa logging.Formatter padrão para garantir que o filtro sozinho protege msg, args e exc_text
        log, stream = self._make_logger("test.nvr.sanitize.filter", use_filter=True, use_formatter=False)

        log.debug("Erro direto: 'rtsp://admin:SegredoMsg@192.168.1.2:554/onvif1'")
        log.debug("Erro com arg: %s (tentativa %d)", "http://admin:SegredoArg%401@192.168.1.2/a", 2)
        log.debug("Erro com dict: %(url)s", {"url": "https://user:SegredoDict%2F2@192.168.1.2/b"})
        log.debug("URL template: rtsp://admin:%s@192.168.1.2:554/live", "SegredoTpl")

        exc = ConnectionRefusedError(
            "[Errno 111] Connection refused: 'rtsp://admin:SegredoExc%2F3@127.0.0.1:1/x'"
        )
        log.debug("Exceção via %%s=%s e %%r=%r", exc, exc)

        try:
            raise ConnectionRefusedError(
                "[Errno 111] Connection refused: 'rtsp://admin:SegredoTb%404@127.0.0.1:1/x'"
            )
        except ConnectionRefusedError:
            log.exception("Falha ao abrir stream RTSP")

        output = stream.getvalue()
        for secret in (
            "SegredoMsg",
            "SegredoArg",
            "SegredoDict",
            "SegredoTpl",
            "SegredoExc",
            "SegredoTb",
        ):
            self.assertNotIn(secret, output)

        self.assertIn("rtsp://admin:****@192.168.1.2:554/onvif1", output)
        self.assertIn("http://admin:****@192.168.1.2/a (tentativa 2)", output)
        self.assertIn("https://user:****@192.168.1.2/b", output)
        self.assertIn("rtsp://admin:****@192.168.1.2:554/live", output)
        self.assertIn("rtsp://admin:****@127.0.0.1:1/x", output)
        self.assertIn("Traceback (most recent call last):", output)

    def test_formatter_alone_masks_formatted_output_and_traceback(self):
        log, stream = self._make_logger("test.nvr.sanitize.formatter", use_filter=False, use_formatter=True)

        try:
            raise RuntimeError("Falha em 'rtsp://admin:FormatterSecret%2F9@10.0.0.5:554/h264'")
        except RuntimeError:
            log.exception("Erro no codec para https://op:OutroSegredo@10.0.0.5/info")

        output = stream.getvalue()
        self.assertNotIn("FormatterSecret", output)
        self.assertNotIn("OutroSegredo", output)
        self.assertIn("rtsp://admin:****@10.0.0.5:554/h264", output)
        self.assertIn("https://op:****@10.0.0.5/info", output)

    def test_filter_and_formatter_mask_exception_with_quote_in_password(self):
        exc_msg = "Connection refused: 'rtsp://admin:ab'cd@host/x'"
        for mode, use_filter, use_formatter in (
            ("filter", True, False),
            ("formatter", False, True),
        ):
            with self.subTest(mode=mode):
                log, stream = self._make_logger(
                    f"test.nvr.sanitize.quote.{mode}",
                    use_filter=use_filter,
                    use_formatter=use_formatter,
                )
                exc = ConnectionRefusedError(exc_msg)
                log.debug("Erro ao conectar: %s", exc)
                try:
                    raise ConnectionRefusedError(exc_msg)
                except ConnectionRefusedError:
                    log.exception("Exceção ao abrir stream")

                output = stream.getvalue()
                self.assertNotIn("ab'cd", output)
                self.assertIn("rtsp://admin:****@host/x", output)

    def test_setup_logging_installs_global_root_sanitization(self):
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        orig_level = root.level
        stderr_buf = io.StringIO()

        try:
            root.handlers = []
            with contextlib.redirect_stderr(stderr_buf):
                setup_logging(debug=True)
                child = logging.getLogger("central_nvr.test.child")
                child.debug(
                    "PyAV stream error: Connection refused: 'rtsp://admin:RootSecret%407@127.0.0.1:1/x'"
                )
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in orig_handlers:
                root.addHandler(h)
            root.setLevel(orig_level)

        output = stderr_buf.getvalue()
        self.assertNotIn("RootSecret", output)
        self.assertIn("rtsp://admin:****@127.0.0.1:1/x", output)


class DiagnoseCameraSanitizationTest(unittest.TestCase):
    """Testes de sanitização de saída no scripts/diagnose_camera.py (Issue #71)."""

    @staticmethod
    def _load_diagnose_module():
        script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "diagnose_camera.py"
        )
        spec = importlib.util.spec_from_file_location("diagnose_camera_under_test", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_format_rtsp_error_masks_password_before_truncation(self):
        mod = self._load_diagnose_module()
        secret = "Sup3rS3nha%2F%40Longa"
        exc = ConnectionRefusedError(
            f"[Errno 111] Connection refused: 'rtsp://admin:{secret}@127.0.0.1:554/onvif1'\nextra"
        )
        formatted = mod.format_rtsp_error(exc)
        self.assertNotIn("Sup3rS3nha", formatted)
        self.assertIn("rtsp://admin:****@127.0.0.1:554/onvif1", formatted)
        self.assertNotIn("\n", formatted)

        # Mensagem longa onde o corte de 120 caracteres cairia no meio da senha se não sanitizasse antes
        long_prefix = "X" * 105
        long_exc = RuntimeError(f"{long_prefix} 'rtsp://admin:CorteNoMeioDaSenha@127.0.0.1:554/x'")
        formatted_long = mod.format_rtsp_error(long_exc, max_len=120)
        self.assertNotIn("Corte", formatted_long)
        self.assertLessEqual(len(formatted_long), 120)

        self.assertEqual(mod.format_rtsp_error(RuntimeError("")), "Sem dados recebidos no tempo limite")

    def test_diagnose_stdout_never_prints_password(self):
        mod = self._load_diagnose_module()
        raw_pwd = "Minha/Senha@Secreta"
        encoded_pwd = urllib.parse.quote(raw_pwd, safe="")

        fake_av = mock.MagicMock()
        fake_av.HTTPUnauthorizedError = type("HTTPUnauthorizedError", (Exception,), {})

        def _failing_open(url, **_kwargs):
            raise ConnectionRefusedError(f"[Errno 111] Connection refused: '{url}'")

        fake_av.open.side_effect = _failing_open

        stdout_buf = io.StringIO()
        with mock.patch.object(mod, "test_port", side_effect=lambda ip, port, timeout=1.0: port == 554), \
             mock.patch.dict(sys.modules, {"av": fake_av}), \
             contextlib.redirect_stdout(stdout_buf):
            mod.diagnose("127.0.0.1", password=raw_pwd)

        output = stdout_buf.getvalue()
        self.assertIn("Resposta:", output)
        self.assertNotIn(raw_pwd, output)
        self.assertNotIn(encoded_pwd, output)
        self.assertIn("rtsp://admin:****@127.0.0.1:554/onvif1", output)

    def test_standalone_fallback_without_central_nvr_package(self):
        blocked = {
            "central_nvr": None,
            "central_nvr.core": None,
            "central_nvr.core.log_sanitize": None,
        }
        with mock.patch.dict(sys.modules, blocked):
            mod = self._load_diagnose_module()

        self.assertIsNot(mod.sanitize_url_credentials, sanitize_url_credentials)

        exc = ConnectionRefusedError(
            "[Errno 111] Connection refused: 'rtsp://admin:ab'cd@e/f:g@10.0.0.1:554/onvif1'"
        )
        formatted = mod.format_rtsp_error(exc)
        self.assertNotIn("ab'cd", formatted)
        self.assertNotIn("e/f:g", formatted)
        self.assertIn("rtsp://admin:****@10.0.0.1:554/onvif1", formatted)

        two = mod.sanitize_url_credentials("'rtsp://a:seg1@h/x' e 'http://b:seg2%2F%40@h2/y'")
        self.assertNotIn("seg1", two)
        self.assertNotIn("seg2", two)
        self.assertEqual(two, "'rtsp://a:****@h/x' e 'http://b:****@h2/y'")
        self.assertEqual(mod.sanitize_url_credentials(""), "")


class StreamWorkerLogSanitizationE2ETest(unittest.TestCase):
    """Teste ponta a ponta: falha no av.open do StreamWorker não vaza senha no log (Issue #71)."""

    def test_pyav_stream_and_hwaccel_fallback_errors_do_not_leak_password(self):
        raw_pwd = "S3nha/PyAV@2026"
        encoded_pwd = urllib.parse.quote(raw_pwd, safe="")
        cam = _cam(raw_pwd)
        stream_url = cam.get_full_rtsp_url()

        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        orig_level = root.level
        stderr_buf = io.StringIO()

        fake_av = mock.MagicMock()
        fake_av.library_versions = {"libavformat": (60, 0, 0)}
        fake_av.error = None
        fake_av.HTTPUnauthorizedError = type("HTTPUnauthorizedError", (Exception,), {})

        def _fake_open(url, *args, **kwargs):
            if kwargs.get("hwaccel") is not None:
                raise RuntimeError(f"VAAPI hwaccel init failed for '{url}'")
            raise ConnectionRefusedError(f"[Errno 111] Connection refused: '{url}'")

        fake_av.open.side_effect = _fake_open

        worker = StreamWorker(cam, hw_accel="vaapi", rtsp_transport="tcp", auto_reconnect=False)
        worker._is_running = True

        try:
            root.handlers = []
            with contextlib.redirect_stderr(stderr_buf):
                setup_logging(debug=True)
                with mock.patch("central_nvr.core.stream_worker.HAS_AV", True), \
                     mock.patch("central_nvr.core.stream_worker.av", fake_av, create=True), \
                     mock.patch.object(worker, "_build_pyav_hwaccel", return_value=object()):
                    worker._run_pyav_stream(stream_url, transport="tcp")
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in orig_handlers:
                root.addHandler(h)
            root.setLevel(orig_level)

        log_output = stderr_buf.getvalue()
        # Garante que os dois caminhos de log de erro foram realmente exercitados
        self.assertIn("Fallback para decodificação em software (CPU)", log_output)
        self.assertIn("PyAV stream error", log_output)
        self.assertNotIn(raw_pwd, log_output)
        self.assertNotIn(encoded_pwd, log_output)
        self.assertIn("rtsp://admin:****@192.168.1.10:554/onvif1", log_output)


if __name__ == "__main__":
    unittest.main()
