"""Display-only values shared by TX reports and workbench calculation snapshots."""
import math
import re

_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_FILL = re.compile(r'SF\((\d{1,2})\)\s*=\s*('+_NUMBER+r')')


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def slot_fills(result):
    """Keep native SF labels: concentric windings may have several slot fills.

    Historical snapshots already contain this line. Read only the native winding
    section, never free-text design notes, and do not recompute from rounded data.
    """
    text=result.get('report_text','')
    if not isinstance(text,str) or '低谐波绕组数据' not in text:return []
    section=text.rsplit('低谐波绕组数据',1)[1].split('计算数据',1)[0]
    values=[]
    for line in section.splitlines():
        matches=list(_FILL.finditer(line))
        if not matches or _FILL.sub('',line).strip():continue
        for match in matches:
            value=float(match[2])
            if finite(value):values.append({'label':f'SF({match[1]})','value':value})
    return values


def material(parameters):
    # Native TX prints the literal DW prefix followed by the entered GGP code.
    code=parameters.get('GGP')
    return f'DW{code:g}' if finite(code) else '—'


def winding(parameters,result):
    """Expose native slot fills and the wire choices used for this calculation."""
    wires=[]
    for group,count_key,diameter_key in [(1,'N6','D6'),(2,'N7','D7')]:
        count,diameter=parameters.get(count_key),parameters.get(diameter_key)
        wires.append({'group':group,'parallel_wires':count if finite(count) else None,
                      'diameter_mm':diameter if finite(diameter) else None,
                      'used':count>0 if finite(count) else None})
    paths=parameters.get('A1')
    return {'material':material(parameters),'wires':wires,'parallel_paths':paths if finite(paths) else None,
            'slot_fills':slot_fills(result)}


def dimensions(parameters):
    """Convert TX centimetre inputs to millimetres, keeping angles and codes."""
    from .tx_parameters import CM
    values={key:value*10 if key in CM and finite(value) else value for key,value in parameters.items()}
    bore,gap=values.get('D3'),values.get('G')
    values['D2']=bore-2*gap if finite(bore) and finite(gap) else None
    return values
