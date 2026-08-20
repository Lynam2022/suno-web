from src.cli import build_parser


def test_parser_subcommands():
    parser = build_parser()
    args = parser.parse_args(["generate", "a tune", "-o", "out",
                              "--instrumental"])
    assert args.cmd == "generate"
    assert args.prompt == "a tune"
    assert args.instrumental is True
    assert args.output == "out"
    assert args.server == "http://localhost:8071"

    args = parser.parse_args(["generate", "--lyrics-file", "l.txt",
                              "--style", "lo-fi", "--title", "夜"])
    assert args.lyrics_file == "l.txt"

    for cmd in ("install", "login", "serve", "health"):
        assert build_parser().parse_args([cmd]).cmd == cmd


def test_server_default_from_env(monkeypatch):
    """SUNO_WEB_SERVER 設了就當預設,沒設回本機。"""
    monkeypatch.setenv("SUNO_WEB_SERVER", "http://192.168.11.11:8071")
    args = build_parser().parse_args(["generate", "x"])
    assert args.server == "http://192.168.11.11:8071"
    assert build_parser().parse_args(["health"]).server == "http://192.168.11.11:8071"

    monkeypatch.delenv("SUNO_WEB_SERVER")
    assert build_parser().parse_args(["generate", "x"]).server == "http://localhost:8071"


def test_install_puts_slash_commands_where_agents_look(tmp_path, monkeypatch, capsys):
    """install 會把 slash command 裝進偵測到的 agent 目錄。"""
    from src import cli
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gemini").mkdir()

    cli._install_commands()

    assert (tmp_path / ".claude/commands/suno-web/suno.md").is_file()
    assert (tmp_path / ".gemini/commands/suno-web/suno.toml").is_file()
    assert "instrumental" in (tmp_path / ".claude/commands/suno-web/suno.md").read_text()


def test_login_takes_a_worker_number():
    args = build_parser().parse_args(["login", "-w", "2"])
    assert args.cmd == "login" and args.worker == 2
    assert build_parser().parse_args(["login"]).worker == 0


def test_worker_profile_dir_naming(monkeypatch, tmp_path):
    """命名沿用 gemini-web:0 用 profiles/,之後是 profiles-N。"""
    from src import config
    monkeypatch.setattr(config.settings, "profile_dir", str(tmp_path / "profiles"))
    assert config.get_worker_profile_dir(0) == str(tmp_path / "profiles")
    assert config.get_worker_profile_dir(1) == str(tmp_path / "profiles-1")
    assert config.get_worker_profile_dir(3) == str(tmp_path / "profiles-3")
