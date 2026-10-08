"""Source-only Hook enrichment; never runs Shell or resolves a live environment.

JSON positions are structural, not searches for repeated key text. Literal
handler output is recognized only in a small, whole-script Shell subset:
comments/blanks, one quoted cat heredoc containing Hook JSON, optional exit 0.
Everything else remains an extraction gap, not a runtime conclusion.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath


def parse_json_spans(text: str):
    """Return strict JSON and key/value offsets indexed by structural key path."""
    if len(text) > 2_000_000:
        raise ValueError("JSON exceeds the bounded static extraction limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("non-JSON numeric constant")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON numeric value exceeds finite representation")
        return number

    decoder = json.JSONDecoder(object_pairs_hook=unique, parse_constant=reject_constant, parse_float=finite_float)
    value = decoder.decode(text)
    spans = {}

    def skip(index):
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    def visit(index, path, key_start=None):
        start = skip(index)
        index = start
        if text[index] == "{":
            index = skip(index + 1)
            while text[index] != "}":
                key_offset = index
                key, index = decoder.raw_decode(text, index)
                index = skip(index)
                assert text[index] == ":"  # Whole JSON already validated.
                index = visit(index + 1, path + (key,), key_offset)
                index = skip(index)
                if text[index] == "}":
                    break
                index = skip(index + 1)
            index += 1
        elif text[index] == "[":
            index = skip(index + 1)
            item = 0
            while text[index] != "]":
                index = visit(index, path + (item,))
                item += 1
                index = skip(index)
                if text[index] == "]":
                    break
                index = skip(index + 1)
            index += 1
        else:
            _, index = decoder.raw_decode(text, index)
        spans[path] = {"start": start, "end": index,
                       "keyStart": start if key_start is None else key_start}
        return index

    visit(0, ())
    return value, spans


def literal_hook_json(text: str):
    """Recognize literal stdin JSON, without asserting cat resolution/execution."""
    # Shell lines end with LF; Python splitlines also treats Unicode separators
    # as boundaries and could turn literal text into an apparent command.
    lines = text.split("\n")
    if "\r" in text:
        return None
    if lines and lines[0].startswith("#!") and not re.fullmatch(
            r"#![ \t]*(?:/usr/bin/env[ \t]+(?:bash|sh)|/(?:usr/)?bin/(?:bash|sh))[ \t]*", lines[0]):
        return None
    cursor = 0

    def ignore(line):
        return not line.strip(" \t") or line.lstrip(" \t").startswith("#")

    while cursor < len(lines) and ignore(lines[cursor]):
        cursor += 1
    if cursor == len(lines):
        return None
    # No unquoted delimiter, substitution, tab stripping, redirection or pipeline.
    match = re.fullmatch(r"[ \t]*cat[ \t]*<<[ \t]*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1[ \t]*", lines[cursor])
    if not match:
        return None
    command_line = cursor + 1
    start = cursor + 1
    cursor = start
    while cursor < len(lines) and lines[cursor] != match.group(2):
        cursor += 1
    if cursor == len(lines):
        raise ValueError("quoted heredoc delimiter is not closed")
    body = "\n".join(lines[start:cursor])
    body_end = cursor
    remainder = [line.strip(" \t") for line in lines[cursor + 1:] if not ignore(line)]
    if remainder not in ([], ["exit 0"]):
        return None
    value, spans = parse_json_spans(body)
    if not isinstance(value, dict) or not isinstance(value.get("hookSpecificOutput"), dict):
        return None
    return {"value": value, "spans": spans, "body": body,
            "commandLine": command_line, "startLine": start + 1,
            "endLine": body_end, "exitStatement": "exit 0" if remainder else None,
            "scriptEndLine": len(text.split("\n")) - (1 if text.endswith("\n") else 0)}


def symbolic_target(command: str, root: Path, files: set[str]):
    """Map one canonical symbolic script path; no shlex or environment expansion."""
    candidate = command
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        candidate = candidate[1:-1]
    # Single quotes suppress the variable expansion, so are deliberately excluded.
    match = re.fullmatch(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)", candidate)
    if not match:
        return None
    relative = match.group(1)
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {".", ".."} for part in relative.split("/")):
        return None
    if PurePosixPath(relative).as_posix() != relative or relative not in files:
        return None
    try:
        (root / relative).resolve().relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return None
    return relative


def enrich(graph: dict, root: Path, contents: dict[str, str], stable, evidence):
    """Add domain declarations using the canonical nodes, edges and Evidence."""
    revision, case_id = graph["revision"], graph["caseId"]

    def component(path):
        return stable("node", f"component:{case_id}:{path}:{revision}")

    def diagnostic(code, path, message, **detail):
        graph["diagnostics"].append({"code": code, "severity": "warning", "path": path,
                                     "message": message, "extensions": detail})

    def add_node(path, identity, kind, label, line_span, quote, attributes):
        ref = evidence(revision, path, line_span, quote)
        graph["evidence"].append(ref)
        node_id = stable("node", identity)
        graph["nodes"].append({"id": node_id, "type": kind, "label": label,
                               "attributes": {**attributes, "staticOnly": True}, "evidence": [ref["id"]]})
        return node_id, ref["id"]

    def edge(source, target, relation, refs, **attributes):
        graph["edges"].append({"id": stable("edge", f"{source}:{relation}:{target}"),
                               "from": source, "to": target, "type": relation,
                               "attributes": {"staticOnly": True, **attributes}, "evidence": refs})

    def location(text, span, base=1):
        first = base + text.count("\n", 0, span["keyStart"])
        last = base + text.count("\n", 0, max(span["start"], span["end"] - 1))
        return str(first) if first == last else f"{first}-{last}"

    for path, text in contents.items():
        if Path(path).name == "hooks.json":
            try:
                config, spans = parse_json_spans(text)
                if not isinstance(config, dict):
                    raise ValueError("hook config root is not an object")
                events = config.get("hooks", config)
                prefix = ("hooks",) if "hooks" in config else ()
                if not isinstance(events, dict):
                    raise ValueError("hook event map is not an object")
            except (ValueError, RecursionError) as error:
                diagnostic("hook-config-unparsed", path, str(error))
                continue
            for event, groups in events.items():
                if not isinstance(groups, list):
                    continue
                event_path = prefix + (event,)
                # Keep the event ID stable across enrichment versions.
                event_id, event_ref = add_node(
                    path, f"hook:{path}:{event}:{revision}", "Scenario", f"hook event {event}",
                    str(text.count("\n", 0, spans[event_path]["keyStart"]) + 1),
                    f"hook event {event} registered", {"event": event})
                edge(component(path), event_id, "registers", [event_ref])
                for group_index, group in enumerate(groups):
                    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                        diagnostic("hook-handler-shape-unresolved", path,
                                   "Event entry is outside the supported nested hooks declaration shape",
                                   event=event, index=group_index)
                        continue
                    group_path = event_path + (group_index,)
                    for index, handler in enumerate(group["hooks"]):
                        if not isinstance(handler, dict) or handler.get("type") != "command":
                            continue
                        command = handler.get("command")
                        if not isinstance(command, str) or not command.strip() or len(command) > 4096:
                            diagnostic("hook-command-unparsed", path, "Command must be a bounded nonempty string")
                            continue
                        key_path = group_path + ("hooks", index)
                        attrs = {"kind": "hook-command-declaration", "hookType": "command",
                                 "hookEvent": event, "command": command, "keyPath": list(key_path),
                                 "environmentResolution": "unknown", "invocation": "unverified"}
                        # Retain group conditions and handler options exactly as declarations.
                        attrs["groupOptions"] = {key: value for key, value in group.items() if key != "hooks"}
                        attrs["handlerOptions"] = {key: value for key, value in handler.items() if key not in {"type", "command"}}
                        command_id, command_ref = add_node(
                            path, f"hook-command:{path}:{key_path!r}:{revision}", "Component",
                            f"{event} command hook registration in {path}", location(text, spans[key_path]),
                            text[spans[key_path]["start"]:spans[key_path]["end"]], attrs)
                        edge(event_id, command_id, "declares", [event_ref, command_ref])
                        target = symbolic_target(command, root, set(contents))
                        if target:
                            # This is a conditional correspondence, not a resolved process env.
                            # Use the command node's Evidence so bounded selection
                            # can preserve the reference rather than dropping an
                            # edge-only Evidence ID from the model request.
                            edge(command_id, component(target), "references", [command_ref],
                                 kind="symbolic-hook-target", symbolicRoot="CLAUDE_PLUGIN_ROOT",
                                 sourceRootCorrespondence=target,
                                 condition="CLAUDE_PLUGIN_ROOT must refer to this source root; host execution is unverified")
                        else:
                            diagnostic("hook-command-target-unresolved", path,
                                       "No canonical in-root symbolic script target; command remains a declaration",
                                       nodeId=command_id)
        if Path(path).suffix != ".sh":
            continue
        try:
            literal = literal_hook_json(text)
        except (ValueError, RecursionError) as error:
            diagnostic("hook-literal-json-unparsed", path, str(error))
            continue
        if literal is None:
            if "hookSpecificOutput" in text:
                diagnostic("hook-literal-output-unresolved", path,
                           "Marker alone does not prove output; script is outside the quoted literal whole-script subset")
            continue
        output_id, output_ref = add_node(
            path, f"hook-literal:{path}:{literal['commandLine']}:{revision}", "Component",
            f"literal Hook handler JSON supplied to cat stdin in {path}",
            f"{literal['commandLine']}-{literal['scriptEndLine']}",
            "\n".join(text.split("\n")[literal['commandLine'] - 1:]),
            {"kind": "hook-literal-json", "literalJSON": literal["value"],
             "shellExpansion": "disabled-by-quoted-delimiter", "commandResolution": "unknown",
             "execution": "unverified", "hostApplication": "unknown",
             "recognizedScriptShape": "comments/blanks + one quoted cat heredoc" +
             (" + exit 0" if literal["exitStatement"] else ""),
             "exitStatement": literal["exitStatement"]})
        edge(component(path), output_id, "contains", [output_ref], kind="literal-stdin-declaration")
        for key, value in literal["value"]["hookSpecificOutput"].items():
            key_path = ("hookSpecificOutput", key)
            field_id, field_ref = add_node(
                path, f"hook-literal-field:{path}:{literal['commandLine']}:{key_path!r}:{revision}", "Component",
                f"Hook handler literal field hookSpecificOutput.{key}",
                location(literal["body"], literal["spans"][key_path], literal["startLine"]),
                literal["body"][literal["spans"][key_path]["keyStart"]:literal["spans"][key_path]["end"]],
                {"kind": "hook-literal-field", "keyPath": list(key_path), "literalValue": value,
                 "execution": "unverified", "hostApplication": "unknown"})
            edge(output_id, field_id, "contains", [field_ref])
    return graph
