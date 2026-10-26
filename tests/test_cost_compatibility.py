"""Synthetic two-group program; no private kernel instructions or data needed."""
from copy import deepcopy
import hashlib
import json

import pytest

from dcsj import cost_compatibility as cc, numeric_vm as nv

FIRST, SECOND, CURRENT = 0x4AFD64, 0x4AFD6C, 0x4AB69C
COUNT, TOTAL = 0x400100, 0x400104


def mem(address):
    return ['m', None, None, 1, address, 4]


def synthetic_program(repeat=False):
    """Count visits, then double that count in an independent synthetic tail."""
    r = ['r', 'eax', 4]
    entry, branch, again, tail, end = cc.ENTRY, cc.BRANCH, cc.AGAIN, cc.TAIL, cc.RETURN
    code = {
        1: (2, 'call', [['i', entry, 4]]),
        2: (3, 'call' if repeat else 'jmp', [['i', entry if repeat else 0xFFFF000, 4]]),
        3: (4, 'jmp', [['i', 0xFFFF000, 4]]),
        entry: (entry+1, 'mov', [r, mem(FIRST)]),
        entry+1: (entry+2, 'mov', [mem(CURRENT), r]),
        entry+2: (entry+3, 'add', [mem(COUNT), ['i', 1, 4]]),
        entry+3: (entry+4, 'fld', [mem(CURRENT)]),
        entry+4: (entry+5, 'fcomp', [mem(FIRST)]),
        entry+5: (entry+6, 'mov', [r, ['i', 0, 4]]),
        entry+6: (entry+7, 'fnstsw', [['r', 'ax', 2]]),
        entry+7: (branch, 'test', [['r', 'ah', 1], ['i', 0x44, 1]]),
        branch: (branch+2, 'jp', [['i', tail, 4]]),
        branch+2: (branch+3, 'mov', [r, mem(SECOND)]),
        branch+3: (again, 'mov', [mem(CURRENT), r]),
        again: (entry+2, 'jmp', [['i', entry+2, 4]]),
        tail: (tail+1, 'mov', [r, mem(COUNT)]),
        tail+1: (tail+2, 'imul', [r, ['i', 2, 4]]),
        tail+2: (end, 'mov', [mem(TOTAL), r]),
        end: (end+1, 'ret', []),
    }
    return {'image_base': 0x400000, 'image_size': 0xC0000, 'stack_base': 0x800000,
            'stack_size': 4096, 'initial_ebp': 0x800800, 'initial_segments': [],
            'entry': 1, 'function_names': {entry: 'COST'}, 'code': code}


def install(monkeypatch, program, *, enable=True):
    # Exercise the real fingerprint gate against a synthetic, test-owned graph.
    rows = [[pc, *row] for pc, row in sorted(program['code'].items()) if cc.ENTRY <= pc < cc.END]
    digest = hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()
    monkeypatch.setattr(cc, 'CODE_SHA256', digest if enable else 'unrecognized')
    monkeypatch.setattr(nv, '_program', lambda path: program)


@pytest.mark.parametrize('repeat', [False, True])
@pytest.mark.parametrize('unused_second', [False, True])
def test_equal_diameters_visit_both_groups_and_original_tail(monkeypatch, repeat, unused_second):
    program = synthetic_program(repeat)
    install(monkeypatch, program)
    overrides = {FIRST: 1.06, SECOND: 1.06, 0x4AFD68: 0. if unused_second else 1.}
    result = nv.run(overrides, max_steps=100)
    visits = 4 if repeat else 2
    assert result['memory'].uint(COUNT) == visits
    assert result['memory'].uint(TOTAL) == visits*2
    assert result['calls']['COST'] == (2 if repeat else 1)
    assert len(result['compatibility_repairs']) == (2 if repeat else 1)
    assert all(r['groups_evaluated'] == 2 and r['id'] == cc.REPAIR_ID for r in result['compatibility_repairs'])
    assert result['memory'].f32(FIRST) == result['memory'].f32(SECOND)
    assert result['memory'].f32(0x4AFD68) == overrides[0x4AFD68]
    # The old graph demonstrably loops at these exact inputs.
    install(monkeypatch, program, enable=False)
    with pytest.raises(nv.NumericKernelLimitError):
        nv.run(overrides, max_steps=100)


@pytest.mark.parametrize('second', [0., .8, 1.0000001192092896, 1.2])
def test_unequal_diameters_are_bit_identical_to_unmodified_dispatch(monkeypatch, second):
    program = synthetic_program(repeat=True)
    install(monkeypatch, program)
    fixed = nv.run({FIRST: 1., SECOND: second})
    install(monkeypatch, program, enable=False)
    original = nv.run({FIRST: 1., SECOND: second})
    for key in ('outputs', 'steps', 'calls', 'completed', 'precision', 'compatibility_repairs'):
        assert fixed[key] == original[key]
    assert fixed['compatibility_repairs'] == []
    assert fixed['memory'].regions == original['memory'].regions


def test_float32_equal_inputs_are_repaired_but_unknown_graph_is_not(monkeypatch):
    program = synthetic_program()
    install(monkeypatch, program)
    r = nv.run({FIRST: 1., SECOND: 1.+1e-9}, max_steps=100)
    assert r['compatibility_repairs'][0]['diameter_mm'] == 1.
    changed = deepcopy(program)
    changed['code'][cc.TAIL+1] = (cc.TAIL+2, 'imul', [['r', 'eax', 4], ['i', 3, 4]])
    assert not cc.matches(changed)
    monkeypatch.setattr(nv, '_program', lambda path: changed)
    with pytest.raises(nv.NumericKernelLimitError):
        nv.run({FIRST: 1., SECOND: 1.}, max_steps=100)


def test_checkpoint_preserves_group_state_without_sharing_repair_log(monkeypatch):
    program = synthetic_program()
    install(monkeypatch, program)
    r = nv.run({FIRST: 1., SECOND: 1.}, capture_at=cc.AGAIN)
    checkpoint = r['checkpoint']
    assert checkpoint.cost_traversal.pass_number == 1
    assert checkpoint.cost_traversal.repairs == []
    assert r['compatibility_repairs']
    while not checkpoint.done and checkpoint.steps < 100:
        checkpoint.steps += 1
        if not checkpoint.callback():
            checkpoint.execute(*program['code'][checkpoint.pc])
    assert checkpoint.done
    assert checkpoint.cost_traversal.pass_number == 0
    assert checkpoint.cost_traversal.repairs == r['compatibility_repairs']
    assert checkpoint.memory.regions == r['memory'].regions


def test_equal_value_alone_cannot_skip_first_group_or_invalid_state(monkeypatch):
    program = synthetic_program()
    install(monkeypatch, program)
    vm = nv._VM(program, {FIRST: 1., SECOND: 1., CURRENT: 1.})
    vm.pc = cc.BRANCH
    vm.fpu_compare = 0x4000
    for phase, parity, comparison in [(0, False, 0x4000), (1, False, 0x4000),
                                       (2, True, 0x4000), (2, False, 0x4500), (3, False, 0x4000)]:
        vm.cost_traversal.pass_number = phase
        vm.pf, vm.fpu_compare = parity, comparison
        assert not vm.cost_traversal.visit(vm)
        assert vm.pc == cc.BRANCH and not vm.cost_traversal.repairs


@pytest.mark.parametrize('repairs', [[], [{'id': cc.REPAIR_ID, 'diameter_mm': 1., 'groups_evaluated': 2}]])
def test_api_freezes_repair_provenance_and_trace(client, parameters, monkeypatch, repairs):
    from dcsj import engine, wire_selection
    from dcsj.kernel_layout import OUTPUT_MAP
    from test_wire_selection import memory
    raw = [0.]*80
    for key, value in {'ATA1': .9, 'PF1': .8, 'Sf': .7}.items():
        raw[OUTPUT_MAP[key]] = value
    monkeypatch.setattr(engine, 'resource_context', lambda: {
        'kernel_path': 'synthetic', 'kernel_sha256': 'synthetic',
        'package_id': None, 'package_version': 'synthetic'})
    monkeypatch.setattr(engine, 'engine_status', lambda context: {'name': 'synthetic', 'missing_assets': []})
    monkeypatch.setattr(engine, 'prepare_memory', lambda p, context: ({}, {'name': 'synthetic', 'kind': 'test', 'slot': 1}))
    monkeypatch.setattr(engine, 'run', lambda *args, **kwargs: {
        'outputs': {'101': raw}, 'memory': memory({k: parameters[k] for k in wire_selection.KEYS}),
        'steps': 50, 'precision': 'synthetic', 'compatibility_repairs': repairs})
    response = client.post('/api/v1/calculate', json={'parameters': parameters, 'persist': True})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['provenance']['compatibility_repairs'] == repairs
    assert any('完成两组查表' in line for line in saved['trace']) == bool(repairs)
    monkeypatch.setattr(engine, 'run', lambda *a, **kw: pytest.fail('Stored snapshots must not recalculate'))
    assert client.get('/api/v1/calculations/'+saved['calculation_id']).json() == saved


@pytest.mark.parametrize('a,b,equal', [(1., 1., True), (1., 2., False), (2., 1., False)])
def test_x87_equality_and_parity_follow_cpu_semantics(a, b, equal):
    vm = nv._VM(synthetic_program(), {FIRST: a, SECOND: b})
    for op, args in [('fld', [mem(FIRST)]), ('fcomp', [mem(SECOND)]),
                     ('fnstsw', [['r', 'ax', 2]]), ('test', [['r', 'ah', 1], ['i', 0x44, 1]])]:
        vm.execute(1, op, args)
    assert vm.branch('jp') is not equal
    assert vm.fpu == [None]*8 and vm.top == 0
