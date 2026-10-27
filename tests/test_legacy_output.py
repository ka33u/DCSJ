"""Synthetic compiled-format records: no original reports or resource literals."""
import struct
from types import SimpleNamespace
import pytest
from dcsj.legacy_output import Collector, decode_format, format_record, fixed
from dcsj.numeric_vm import Memory


def memory():
    return Memory({'image_base':0x1000,'image_size':0x5000,'stack_base':0x10000,
                   'stack_size':0x1000,'initial_segments':[]})


def words(*values):return struct.pack('<'+'I'*len(values),*values)


def test_compiled_format_groups_precision_and_right_spacing():
    m=memory()
    # ("A=",F6.2,TR1,2(1X,F5.1),/,A4)
    m.write(0x1100,words(54,(2<<16)|28)+b'A=\0\0'+
            words((2<<24)|33,1,6,11,1,1,(2<<16)|56,14,1,1,(1<<24)|33,1,5,57,(1<<16)|71,29,1,4,55))
    tokens=decode_format(m,0x1100)
    assert format_record(tokens,[3.85,2.4,-1.2,'TEST'])=='A=  3.85    2.4  -1.2\nTEST'
    assert fixed(.1234,5,4)=='.1234'
    assert fixed(12,3,0)=='12.'
    assert fixed(123.456,4,2)=='****'
    assert fixed(None,4,2)=='   -'
    with pytest.raises(ValueError):format_record(tokens,[3.85])
    with pytest.raises(ValueError):format_record(tokens,[3.85,2.4,-1.2,'TEST',99])


def test_collector_observes_own_arguments_without_mutating_memory():
    m=memory();sp=0x10000
    m.write(0x1100,words(54,(2<<16)|28)+b'X=\0\0'+words((2<<24)|33,1,6,14,1,1,29,1,3,55))
    m.write(0x1200,words(0x2011a,0));m.write(0x1210,words(0x10438,0))
    m.set_float(0x1300,4.25);m.write(0x1310,words(3,0x1400));m.write(0x1400,b'ABC')
    vm=SimpleNamespace(pc=0x42fbc0,memory=m,reg=lambda _:sp)
    m.write(sp,words(0,0,2,0,0x1200,0x1300,0x1100))
    collector=Collector();before=[bytes(b) for _,b in m.regions];collector.observe(vm)
    assert [bytes(b) for _,b in m.regions]==before
    vm.pc=0x430220;m.write(sp,words(0,0,0x1210,0x1310))
    before=[bytes(b) for _,b in m.regions];collector.observe(vm)
    assert [bytes(b) for _,b in m.regions]==before
    assert collector.result()['text']=='X=  4.25 ABC'
    assert collector.result()['status']=='complete'
    # Writes to other units never enter the printable calculation sheet.
    vm.pc=0x42fbc0;m.write(sp,words(0,0,10,0,0x1200,0x1300,0x1100));collector.observe(vm)
    assert collector.result()['text']=='X=  4.25 ABC'


def test_unknown_or_oversized_formats_fail_closed_without_blocking_calculation():
    m=memory();sp=0x10000
    m.write(0x1100,words(54,0x9999));m.write(sp,words(0,0,2,0,0,0,0x1100))
    vm=SimpleNamespace(pc=0x42fbc0,memory=m,reg=lambda _:sp)
    collector=Collector();collector.observe(vm)
    assert collector.result()=={'format_version':1,'status':'unavailable'}
    m.write(0x1100,words(54,(5000<<16)|71,55))
    with pytest.raises(ValueError,match='bounds'):decode_format(m,0x1100)
    m.write(0x1100,words(54,(256<<16)|56,(256<<16)|56,14,256,2,57,57,55))
    with pytest.raises(ValueError):decode_format(m,0x1100)
