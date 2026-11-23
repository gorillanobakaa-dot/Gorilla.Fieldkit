import json


def emit(as_json, verdict, message, next_step, data=None, lines=()):
    if as_json:
        print(json.dumps({"verdict": verdict, "message": message, "next": next_step, **(data or {})}, indent=2,
                         default=str))
        return
    for ln in lines:
        print(ln)
    print(f"{verdict}: {message}")
    print(f"NEXT: {next_step}")
