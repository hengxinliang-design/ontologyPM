"""Check the external vocabulary mapping register (references/vocabulary-mappings.yaml).

The internal ontology is the authority: a mapping may only point an existing internal element at a pinned,
whitelisted external term of the right kind, never claims identity (owl:sameAs / equivalent*), and a property
under a security marking must say it respects that marking (export never widens access).

Run from the backend directory:
    .venv/bin/python ../.claude/skills/tsg-ontology/scripts/check_vocabulary_mappings.py [register.yaml]
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path.cwd()))
try:
    from app.ontology import load_ontology
except ModuleNotFoundError:
    sys.exit("Run this script from TSG-MVP/backend with its virtualenv (.venv/bin/python).")

DEFAULT_REGISTER = Path(__file__).resolve().parents[1] / "references" / "vocabulary-mappings.yaml"
REQUIRED = ("mappingId", "internalElement", "kind", "externalTerm", "direction", "relation", "internalVersion", "status")
KINDS = {"type": "class", "property": "property", "link": "property"}
DIRECTIONS = {"internal-to-external", "external-to-internal"}
RELATIONS = {"reference", "broader", "narrower", "exact"}
STATUSES = {"candidate", "approved", "rejected"}
# Identity claims are never registered: the internal model stays the authority.
FORBIDDEN = ("owl:sameAs", "owl:equivalentClass", "owl:equivalentProperty")


def check(register: dict[str, Any], ontology=None) -> list[str]:
    ontology = ontology or load_ontology()
    errors: list[str] = []
    vocabularies = register.get("vocabularies") or {}
    for prefix, vocab in vocabularies.items():
        for field in ("namespace", "version", "source", "verifiedAt"):
            if not vocab.get(field):
                errors.append(f"词汇 {prefix} 缺少 {field}")
    terms = register.get("terms") or {}
    artifacts = {name: set(fields) for name, fields in (register.get("artifacts") or {}).items()}
    seen: set[str] = set()
    for index, mapping in enumerate(register.get("mappings") or []):
        mid = mapping.get("mappingId") or f"#{index + 1}"
        where = f"映射 {mid}"
        missing = [f for f in REQUIRED if not mapping.get(f)]
        if missing:
            errors.append(f"{where} 缺少 {missing}")
            continue
        if mid in seen:
            errors.append(f"{where} 编号重复")
        seen.add(mid)
        term, kind = mapping["externalTerm"], mapping["kind"]
        if term in FORBIDDEN:
            errors.append(f"{where} 不允许登记身份等同 {term}")
            continue
        prefix = term.split(":", 1)[0]
        if prefix not in vocabularies:
            errors.append(f"{where} 前缀 {prefix} 不在已固定版本的词汇中")
        if term not in terms:
            errors.append(f"{where} 外部词条 {term} 不在白名单中（先对照官方页面核对再加入 terms）")
        elif kind in KINDS and terms[term] != KINDS[kind]:
            errors.append(f"{where} 类别不符：{kind} 映射需要外部 {KINDS[kind]}，{term} 是 {terms[term]}")
        for field, allowed in (("kind", set(KINDS)), ("direction", DIRECTIONS), ("relation", RELATIONS), ("status", STATUSES)):
            if mapping[field] not in allowed:
                errors.append(f"{where} {field}={mapping[field]!r} 无效，可选 {sorted(allowed)}")
        errors += [f"{where} {e}" for e in _internal_errors(ontology, artifacts, mapping)]
        status = mapping["status"]
        if status == "rejected" and not mapping.get("rejectionReason"):
            errors.append(f"{where} 已拒绝的映射必须写 rejectionReason")
        if status == "approved":
            if not (mapping.get("reviewer") and mapping.get("approvedAt")):
                errors.append(f"{where} 已批准的映射必须有 reviewer 和 approvedAt")
            if str(mapping["internalVersion"]) != ontology.version:
                errors.append(f"{where} 批准时的本体版本 {mapping['internalVersion']} 与当前 {ontology.version} 不一致，需重新审核")
        if status != "rejected" and not mapping.get("condition"):
            errors.append(f"{where} 必须写适用条件 condition")
    return errors


def _internal_errors(ontology, artifacts: dict[str, set[str]], mapping: dict[str, Any]) -> list[str]:
    element, kind = str(mapping["internalElement"]), mapping["kind"]
    if element.startswith("artifact:"):
        name, _, field = element.removeprefix("artifact:").partition(".")
        if name not in artifacts or (field and field not in artifacts[name]):
            return [f"内部工件字段 {element} 不存在"]
        return [] if (kind == "property") == bool(field) else [f"{element} 与类别 {kind} 不符"]
    if "@" in element:
        source, _, link = element.partition("@")
        if kind != "link":
            return [f"{element} 是关系，类别应为 link"]
        if not any(l.source_type == source and l.api_name == link for l in ontology.link_types):
            return [f"内部关系 {element} 不存在"]
        return []
    type_name, _, prop = element.partition(".")
    definition = ontology.object_type(type_name)
    if definition is None:
        return [f"内部对象类型 {type_name} 不存在"]
    if not prop:
        return [] if kind == "type" else [f"{element} 是对象类型，类别应为 type"]
    if kind != "property":
        return [f"{element} 是属性，类别应为 property"]
    definition_prop = definition.property(prop)
    if definition_prop is None:
        return [f"内部属性 {element} 不存在"]
    if definition_prop.marking and mapping.get("respectsMarking") != definition_prop.marking:
        return [f"{element} 带 {definition_prop.marking} 标记，映射须声明 respectsMarking: {definition_prop.marking}（导出先按标记脱敏）"]
    return []


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REGISTER
    register = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = check(register)
    mappings = register.get("mappings") or []
    counts = {s: sum(m.get("status") == s for m in mappings) for s in sorted(STATUSES)}
    print(f"映射登记 {path.name} v{register.get('registerVersion')}：{len(mappings)} 条，{counts}")
    for error in errors:
        print(f"  ✗ {error}")
    print("通过" if not errors else f"不通过：{len(errors)} 个问题")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
