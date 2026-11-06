"""Ownership and field-level changes derived from immutable design revisions."""
import json

from .parameters import INPUT_KEYS

# Revision one retains the original signature even for old multi-author histories.
MAIN_OWNER = '(SELECT author FROM project_revisions WHERE project_id=projects.id AND revision=1)'
TX_OWNER = '(SELECT author FROM tx_revisions WHERE design_id=tx_designs.id AND revision=1)'


class OwnershipError(ValueError):
    pass


def require_owner(owner, actor):
    if not owner or actor != owner:
        raise OwnershipError('只有原作者可以修改此设计。请另存为自己的方案。')


def copy_origin(source, identity, revision):
    return {**source, 'copied_from': {k: revision[k] for k in ('name', 'author', 'revision')} | {'id': identity}}


def _fields(row):
    fields = {k: row[k] for k in ('name', 'series', 'notes') if k in row.keys()}
    for group in ('parameters', 'slot_bindings'):
        for key, value in json.loads(row[group]).items():
            fields[f'{group}.{key}'] = value
    origin = json.loads(row['lineage'] if 'lineage' in row.keys() else row['source'])
    fields['base_model'] = {k: v for k, v in origin.items() if k != 'copied_from'}
    return fields


_NUMERIC_FIELDS = {'parameters.' + k for k in INPUT_KEYS if k not in {'SizeX','Paihao','Jiefa','Jueyuan'}}
_NUMERIC_FIELDS.update(('parameters.pfw_ref_rpm', 'parameters.pfw_speed_exponent'))


def changes(previous, current):
    if previous is None:
        return []
    before, after = _fields(previous), _fields(current)
    result = []
    for key in sorted(before.keys() | after.keys()):
        if key in before and key in after:
            a, b = before[key], after[key]
            if a == b:
                continue
            if key in _NUMERIC_FIELDS:
                try:
                    if float(a) == float(b):
                        continue
                except (TypeError, ValueError, OverflowError):
                    pass
        entry = {'field': key}
        if key in before:
            entry['before'] = before[key]
        if key in after:
            entry['after'] = after[key]
        result.append(entry)
    return result


def describe(row, previous):
    origin = json.loads(row['lineage'] if 'lineage' in row.keys() else row['source'])
    return {'previous_revision': previous['revision'] if previous is not None else None,
            'changes': changes(previous, row), 'copied_from': origin.get('copied_from')}
