'use strict';
window.DesignVersions = (() => {
  function owned(design) {
    return design && design.owner === window.DCSJAuth.username();
  }
  function editable(design) {
    return owned(design) && !design.archived && !design.copyOnly &&
      (design.latest_revision == null || design.revision === design.latest_revision);
  }
  function copyReference(d) {
    return 'saved-design:' + d.id + '@' + d.revision;
  }
  function describe(d, fields) {
    const box = document.createElement('div');
    if (d.copied_from) {
      const parent = d.copied_from, note = document.createElement('p');
      note.textContent = '另存自「' + parent.name + '」 v' + parent.revision + ' · ' + (parent.author || '未署名');
      box.append(note);
    }
    const details = document.createElement('details'), title = document.createElement('summary');
    const changes = d.changes || [];
    if (d.previous_revision == null) {
      const note = document.createElement('p');
      note.textContent = d.revision === 1 ? '首版' : '没有可比较的上一版';
      box.append(note);
      return box;
    }
    title.textContent = '相对 v' + d.previous_revision + '：' + (changes.length ? changes.length + ' 项变更' : '没有字段变更');
    details.append(title);
    const labels = {name:'设计名称', series:'系列', notes:'设计备注', base_model:'来源型号',
      'slot_bindings.stator':'定子槽型', 'slot_bindings.rotor':'转子槽型'};
    const display = (entry, key) => !(key in entry) ? '（无此项）' : entry[key] === '' ? '（空）' :
      typeof entry[key] === 'object' ? JSON.stringify(entry[key]) : String(entry[key]);
    const list = document.createElement('ul');
    for (const change of changes) {
      const key = change.field.replace(/^parameters\./, '');
      const label = fields.find(f => f.key === key)?.label;
      const li = document.createElement('li');
      li.textContent = (labels[change.field] || (label && label !== key ? label + ' (' + key + ')' : key)) +
        '：' + display(change,'before') + ' → ' + display(change,'after');
      list.append(li);
    }
    if (changes.length) details.append(list);
    box.append(details);
    return box;
  }
  return {owned, editable, copyReference, describe};
})();
