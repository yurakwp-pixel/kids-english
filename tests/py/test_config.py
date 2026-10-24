import pytest

from server.config import Config, load_config, parse_dotenv, scrub_api_key_env, scrub_llm_env


def test_parse_dotenv_ignores_comments_and_quotes():
    env = parse_dotenv('# c\nA=1\nB="two words"\n\nC=\n')
    assert env == {"A": "1", "B": "two words", "C": ""}


def test_scrub_api_key_env_removes_key():
    environ = {"ANTHROPIC_API_KEY": "x", "PATH": "p"}
    assert scrub_api_key_env(environ) is True
    assert "ANTHROPIC_API_KEY" not in environ and environ["PATH"] == "p"


def test_load_config_defaults_and_flags(tmp_path):
    cfg = load_config(tmp_path, ["--fake-llm", "--port", "9000"], environ={})
    assert (cfg.host, cfg.port, cfg.model, cfg.use_fake_llm, cfg.oauth_token) == (
        "127.0.0.1", 9000, "haiku", True, None)


def test_load_config_reads_token_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=tok\n", encoding="utf-8")
    assert load_config(tmp_path, [], environ={}).oauth_token == "tok"


def test_parse_dotenv_skips_lines_without_equals_and_keeps_inner_equals():
    assert parse_dotenv("junk\nURL=http://x/?a=b\n=novalue\n") == {"URL": "http://x/?a=b"}


def test_parse_dotenv_strips_only_a_matching_quote_pair():
    assert parse_dotenv("A='x y'\nB=\"it's\"\nC='open\n") == {"A": "x y", "B": "it's", "C": "'open"}


def test_scrub_api_key_env_also_removes_auth_token_and_base_url():
    # AUTH_TOKEN 은 인증 순서에서 구독 OAuth 보다 앞서고, BASE_URL 은 요청을 다른 곳으로 보낼 수 있다.
    environ = {"ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "http://proxy", "PATH": "p"}
    assert scrub_api_key_env(environ) is True
    assert environ == {"PATH": "p"}


def test_scrub_llm_env_removes_auth_and_parent_claude_session_vars():
    # Claude Code(데스크톱 미리보기, 터미널 패널) 안에서 서버를 띄우면 부모 세션 변수가 따라온다.
    environ = {
        "ANTHROPIC_API_KEY": "x", "ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "x",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "x", "CLAUDECODE": "1", "CLAUDE_CODE_SIMPLE": "1",
        "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_MESSAGING_SOCKET": "m", "CLAUDE_EFFORT": "high",
        "CLAUDE_AGENT_SDK_VERSION": "v",
        "CLAUDE_CODE_OAUTH_TOKEN": "keep", "CLAUDE_CODE_GIT_BASH_PATH": "keep", "CLAUDE_CONFIG_DIR": "keep",
        "PATH": "p", "TEMP": "t",
    }
    removed = scrub_llm_env(environ)
    assert sorted(removed) == sorted([
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDECODE", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION"])
    assert environ == {"CLAUDE_CODE_OAUTH_TOKEN": "keep", "CLAUDE_CODE_GIT_BASH_PATH": "keep",
                       "CLAUDE_CONFIG_DIR": "keep", "PATH": "p", "TEMP": "t"}
    assert scrub_llm_env(environ) == []


def test_scrub_api_key_env_reports_false_when_nothing_to_remove():
    environ = {"PATH": "p"}
    assert scrub_api_key_env(environ) is False
    assert environ == {"PATH": "p"}


def test_load_config_defaults_without_dotenv_or_flags(tmp_path):
    cfg = load_config(tmp_path, [], environ={})
    assert cfg == Config(root=tmp_path)
    assert (cfg.host, cfg.port, cfg.llm_timeout_s, cfg.use_fake_llm) == ("127.0.0.1", 8771, 8.0, False)


@pytest.mark.parametrize("host, expected", [
    ("127.0.0.1", "127.0.0.1"), ("localhost", "localhost"), ("::1", "::1"), ("LOCALHOST", "localhost"),
])
def test_load_config_host_flag_accepts_loopback(tmp_path, host, expected):
    assert load_config(tmp_path, ["--host", host], environ={}).host == expected


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.0.10", "127.0.0.2", "example.com", ""])
def test_load_config_rejects_non_loopback_host(tmp_path, capsys, host):
    # 서버는 이 PC 안(127.0.0.1)에서만 열린다. 집 안 다른 기기로 여는 것은 나중 마일스톤에서 따로 정한다.
    with pytest.raises(SystemExit) as e:
        load_config(tmp_path, ["--host", host], environ={})
    assert e.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_environ_token_wins_over_dotenv(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=from-file\n", encoding="utf-8")
    cfg = load_config(tmp_path, [], environ={"CLAUDE_CODE_OAUTH_TOKEN": "from-env"})
    assert cfg.oauth_token == "from-env"


def test_dotenv_with_bom_and_blank_token(tmp_path):
    (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfCLAUDE_CODE_OAUTH_TOKEN=\n")
    assert load_config(tmp_path, [], environ={}).oauth_token is None
    (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfCLAUDE_CODE_OAUTH_TOKEN=tok\n")
    assert load_config(tmp_path, [], environ={}).oauth_token == "tok"


def test_config_repr_never_shows_the_token(tmp_path):
    cfg = load_config(tmp_path, [], environ={"CLAUDE_CODE_OAUTH_TOKEN": "super-secret-value"})
    assert cfg.oauth_token == "super-secret-value"
    assert "super-secret-value" not in repr(cfg) and "super-secret-value" not in str(cfg)
