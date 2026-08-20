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
