"""CLI entry point: `python -m banker <workbook.xlsx>`."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from .agent import fill_values, run_agent


@click.command()
@click.argument("workbook", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), default=None,
              help="Output JSON path. Defaults to <workbook>.extracted.json")
@click.option("--model", default="claude-sonnet-4-5", help="Claude model ID.")
@click.option("--max-turns", type=int, default=30)
@click.option("--context", default=None,
              help="Optional analyst context string passed to the agent.")
@click.option("-v", "--verbose", is_flag=True)
def main(workbook: Path, out: Path, model: str, max_turns: int,
         context: str, verbose: bool) -> None:
    """Extract structured data from a financial model workbook."""
    # Load .env from cwd or banker-agent dir
    for candidate in [Path(".env"), Path(__file__).parent.parent / ".env"]:
        if candidate.exists():
            load_dotenv(candidate, override=True)
            break

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    out = out or workbook.with_suffix(".extracted.json")

    click.echo(f"Running banker agent on: {workbook}")
    click.echo(f"Model: {model}")
    click.echo(f"Output: {out}")
    click.echo()

    result = run_agent(workbook, model=model, max_turns=max_turns, extra_context=context)

    filled = fill_values(result.draft, workbook, model_used=model)
    filled.tokens_input = result.tokens_in
    filled.tokens_output = result.tokens_out
    filled.agent_turns = result.turns

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(filled.model_dump_json(indent=2))

    click.echo("=" * 60)
    click.echo(f"Turns:              {result.turns}")
    click.echo(f"Tokens in/out:      {result.tokens_in} / {result.tokens_out}")
    click.echo(f"Sheets:             {len(filled.sheets)}")
    click.echo(f"Line items:         {len(filled.line_items)}")
    click.echo(f"Assumptions:        {len(filled.assumptions)}")
    click.echo(f"Narratives:         {len(filled.narratives)}")
    required_found = sum(1 for v in filled.deal_input_fields.found_in_model.values() if v)
    click.echo(f"Required-core hits: {required_found}")
    click.echo(f"Wrote:              {out}")


if __name__ == "__main__":
    main()
