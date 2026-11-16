"""Single operator entrypoint: python -m app.cli <group> <command> [options]."""

import asyncio
import json
import sys
from types import ModuleType

from app.cli import ask, cv, ingest, search, translate

GROUPS: dict[str, ModuleType] = {
    "ingest": ingest,
    "search": search,
    "ask": ask,
    "cv": cv,
    "translate": translate,
}


def usage() -> str:
    lines = ["usage: python -m app.cli <group> <command> [options]", "", "groups:"]
    lines += [f"  {name:<10} {module.parser().description}" for name, module in GROUPS.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in GROUPS:
        print(usage(), file=sys.stderr)
        raise SystemExit(0 if argv and argv[0] in {"-h", "--help"} else 2)
    module = GROUPS[argv[0]]
    args = module.parser().parse_args(argv[1:])
    try:
        value = asyncio.run(module.run(args))
    except Exception as exc:
        # Safe classification only: DB/provider exceptions may carry secrets or job text.
        print(json.dumps({"error": type(exc).__name__, "hint": module.HINT}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
