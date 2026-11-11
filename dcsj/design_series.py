"""Atomic series edits that preserve design inputs and immutable history."""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .design_history import MAIN_OWNER, TX_OWNER, require_owner
from .saved_catalog import series_name
from .storage import ConflictError, NotFoundError, audit, connect, now


class Target(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1, strict=True)


class SeriesUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    series: str = Field(max_length=80)
    items: list[Target] = Field(min_length=1, max_length=200)
    validate_series = field_validator('series')(series_name)

    @field_validator('items')
    @classmethod
    def unique_items(cls, items):
        if len({item.id for item in items}) != len(items):
            raise ValueError('不能重复选择同一设计')
        return items


def update(module, body, actor):
    """Validate the entire batch first; copy stored fields without recalculation."""
    if module not in ('main', 'tx'):
        raise ValueError('未知设计模块')
    table, owner = ('projects', MAIN_OWNER) if module == 'main' else ('tx_designs', TX_OWNER)
    stamp, result = now(), []
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = []
        for target in body.items:
            row = db.execute(f'SELECT *,{owner} AS owner FROM {table} WHERE id=?', (target.id,)).fetchone()
            if row is None:
                raise NotFoundError('所选设计不存在，请刷新列表')
            require_owner(row['owner'], actor)
            if row['revision'] != target.expected_revision or (module == 'main' and row['archived']):
                raise ConflictError('所选设计已有新版本或已归档，本次未修改任何设计。请刷新列表后重试。')
            rows.append(row)
        for row in rows:
            changed = row['series'] != body.series
            revision = row['revision'] + int(changed)
            if changed:
                if module == 'main':
                    db.execute('INSERT INTO project_revisions '
                               '(project_id,revision,name,parameters,notes,created_at,lineage,author,series,slot_bindings) '
                               'SELECT project_id,?,name,parameters,notes,?,lineage,?,?,slot_bindings '
                               'FROM project_revisions WHERE project_id=? AND revision=?',
                               (revision, stamp, actor, body.series, row['id'], row['revision']))
                else:
                    db.execute('INSERT INTO tx_revisions '
                               '(design_id,revision,parameters,source,created_at,author,series,slot_bindings) '
                               'SELECT design_id,?,parameters,source,?,?,?,slot_bindings '
                               'FROM tx_revisions WHERE design_id=? AND revision=?',
                               (revision, stamp, actor, body.series, row['id'], row['revision']))
                db.execute(f'UPDATE {table} SET series=?,revision=?,updated_at=?,author=? WHERE id=?',
                           (body.series, revision, stamp, actor, row['id']))
                audit(db, 'project.series_changed' if module == 'main' else 'tx.series_changed', row['id'],
                      {'actor': actor, 'before': row['series'], 'after': body.series, 'revision': revision})
            result.append({'id': row['id'], 'name': row['name'], 'series': body.series,
                           'previous_series': row['series'], 'revision': revision,
                           'previous_revision': row['revision'], 'changed': changed,
                           'changes': [{'field': 'series', 'before': row['series'], 'after': body.series}] if changed else [],
                           'updated_at': stamp if changed else row['updated_at']})
    count = sum(item['changed'] for item in result)
    return {'items': result, 'changed_count': count, 'unchanged_count': len(result) - count}
