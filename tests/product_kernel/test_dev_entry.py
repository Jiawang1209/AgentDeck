from agentdeck.cli import build_parser


def test_hidden_product_entry_is_parseable() -> None:
    args = build_parser().parse_args(["_product", "--diagnostic"])

    assert args.command == "_product"
    assert args.diagnostic is True


def test_hidden_product_entry_is_absent_from_public_help() -> None:
    help_text = build_parser().format_help()

    assert "_product" not in help_text
    assert "doctor" in help_text
    assert "status" in help_text


def test_product_bootstrap_diagnostic_is_human_text(capsys) -> None:
    from agentdeck.product.bootstrap import run_product_dev

    assert run_product_dev(diagnostic=True) == 0
    assert capsys.readouterr().out == (
        "AgentDeck Product Kernel development entry: ready\n"
    )
