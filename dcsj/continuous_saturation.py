"""Opt-in torque-only saturation model using runtime knots, without extrapolation.

PCHIP slopes follow Fritsch & Butland (1984), DOI:10.1137/0905021.
No recovered table values are embedded here. This changes the torque model;
it is not a more accurate reproduction of the original interpolation routine.
"""
import bisect
import math
from .numeric_vm import NumericKernelError

NOTE = ('试验模型：仅对独立转矩采样的饱和系数改用保形三次插值（PCHIP），'
        '保留原表节点，并连接原低饱和解析段；表外不外推。'
        '电流和第二迭代状态的差均须小于 0.00001。'
        '插值规则已改变，结果不能视为原软件复算或实测精度提升；'
        '常规效率、损耗、TM1 与原曲线仍按原模型计算。有限采样不保证连续全局峰值。')


class MonotoneCubic:
    def __init__(self, x, y):
        self.x, self.y = tuple(x), tuple(y)
        if (len(x) < 2 or len(x) != len(y) or
                not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (*x, *y)) or
                any(b <= a for a, b in zip(x, x[1:]))):
            raise NumericKernelError('连续饱和插值节点须为有限数值，横轴须严格递增且与纵轴等长')
        h = [b-a for a, b in zip(x, x[1:])]
        s = [(b-a)/width for a, b, width in zip(y, y[1:], h)]
        if len(x) == 2:
            self.d = (s[0], s[0])
        else:
            def endpoint(h0, h1, s0, s1):
                slope = ((2*h0+h1)*s0-h0*s1)/(h0+h1)
                if slope*s0 <= 0:
                    return 0.
                return 3*s0 if s0*s1 < 0 and abs(slope) > abs(3*s0) else slope
            slopes = [endpoint(h[0], h[1], s[0], s[1])]
            for i in range(1, len(x)-1):
                a, b = s[i-1:i+1]
                w1, w2 = 2*h[i]+h[i-1], h[i]+2*h[i-1]
                slopes.append(0. if a*b <= 0 else (w1+w2)/(w1/a+w2/b))
            self.d = (*slopes, endpoint(h[-1], h[-2], s[-1], s[-2]))
        if not all(math.isfinite(v) for v in self.d):
            raise NumericKernelError('连续饱和插值斜率无效')

    def interval(self, q):
        if not math.isfinite(q) or not self.x[0] <= q <= self.x[-1]:
            raise NumericKernelError('连续饱和插值超出原表范围，不进行外推')
        return max(0, min(bisect.bisect_right(self.x, q)-1, len(self.x)-2))

    def __call__(self, q):
        i = self.interval(q)
        h = self.x[i+1]-self.x[i]
        t = (q-self.x[i])/h
        return ((2*t**3-3*t**2+1)*self.y[i] + (t**3-2*t**2+t)*h*self.d[i]
                + (-2*t**3+3*t**2)*self.y[i+1] + (t**3-t**2)*h*self.d[i+1])


class Saturation:
    """Apply only at the verified SATU return from its interpolation call."""
    def __init__(self):
        self.interpolator = None

    def apply(self, memory):
        if self.interpolator is None:
            x = [memory.f32(0x4915a4)] + [memory.f32(0x4ab4e4+4*i) for i in range(7)]
            y = [memory.f32(0x49159c)] + [memory.f32(0x4ab4c8+4*i) for i in range(7)]
            if any(v <= 0 for v in y) or any(b > a for a, b in zip(y, y[1:])):
                raise NumericKernelError('连续饱和插值需要正值且不递增的原饱和表')
            self.interpolator = MonotoneCubic(x, y)
        q = memory.f32(0x4ab4bc)
        if not math.isfinite(q) or q < 0:
            raise NumericKernelError('连续饱和插值输入无效')
        if q < self.interpolator.x[0]:
            # The original analytic branch runs after this hook and owns this region.
            return -1
        memory.set_float(0x4af548, self.interpolator(q))
        return self.interpolator.interval(q)
