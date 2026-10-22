from server.config import Config, load_config, parse_dotenv, scrub_api_key_env


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


def test_scrub_api_key_env_reports_false_when_nothing_to_remove():
    environ = {"PATH": "p"}
    assert scrub_api_key_env(environ) is False
    assert environ == {"PATH": "p"}


def test_load_config_defaults_without_dotenv_or_flags(tmp_path):
    cfg = load_config(tmp_path, [], environ={})
    assert cfg == Config(root=tmp_path)
    assert (cfg.host, cfg.port, cfg.llm_timeout_s, cfg.use_fake_llm) == ("127.0.0.1", 8771, 8.0, False)


def test_load_config_host_flag(tmp_path):
    assert load_config(tmp_path, ["--host", "0.0.0.0"], environ={}).host == "0.0.0.0"


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
