"""Standalone ridge fit using Householder QR; no external numerical runtime.

Fixed regularization matches the research prototype. Coefficients are fitted
only from past observations. This module never evaluates or accepts a design.
"""
import math

KEYS=('bs1','hs12','br1','L','ZZ','Djj')
RIDGE=1e-6


def basis(x):
    return [1.,*x,*[x[i]*x[j] for i in range(len(x)) for j in range(i,len(x))]]


def least_squares(matrix,targets):
    """Solve full-column-rank least squares with orthogonal reflections."""
    a=[list(row) for row in matrix];b=[list(row) for row in targets]
    m,n=len(a),len(a[0]);outputs=len(b[0])
    if m<n or len(b)!=m:raise ValueError('Invalid least-squares shape')
    for j in range(n):
        vector=[a[i][j] for i in range(j,m)]
        norm=math.sqrt(math.fsum(v*v for v in vector))
        if not math.isfinite(norm) or norm<=1e-14:raise ValueError('Degenerate ridge fit')
        diagonal=-math.copysign(norm,vector[0])
        vector[0]-=diagonal
        beta=2/math.fsum(v*v for v in vector)
        for col in range(j+1,n):
            factor=beta*math.fsum(v*a[i][col] for i,v in enumerate(vector,j))
            for i,v in enumerate(vector,j):a[i][col]-=factor*v
        for col in range(outputs):
            factor=beta*math.fsum(v*b[i][col] for i,v in enumerate(vector,j))
            for i,v in enumerate(vector,j):b[i][col]-=factor*v
        a[j][j]=diagonal
        for i in range(j+1,m):a[i][j]=0.
    coefficients=[[0.]*outputs for _ in range(n)]
    for i in range(n-1,-1,-1):
        for col in range(outputs):
            coefficients[i][col]=(b[i][col]-math.fsum(a[i][j]*coefficients[j][col]
                for j in range(i+1,n)))/a[i][i]
    if not all(math.isfinite(x) for row in coefficients for x in row):
        raise ValueError('Non-finite model coefficients')
    return coefficients


class QuadraticModel:
    def __init__(self,request,observations):
        self.metrics=sorted({'ATA1','PF1','Sf','Sn','CostFx','Pcu','Pal','Pfe','Pfw1','Ps1',
                            *[c['metric'] for c in request['constraints']]})
        axes={v['key']:v['values'] for v in request['variables']}
        self.lower=[min(axes[k]) for k in KEYS]
        self.span=[max(axes[k])-min(axes[k]) for k in KEYS]
        if not all(math.isfinite(s) and s>0 for s in self.span):raise ValueError('Every model axis needs distinct levels')
        self.rows=[r for r in observations if not r.get('error') and all(
            isinstance(r['values'].get(k),(int,float)) and math.isfinite(r['values'][k]) for k in self.metrics)]
        self.columns=28
        self.rank=None  # No SVD diagnostic; not used in candidate scheduling.
        self.coefficients=None
        if len(self.rows)<24:return
        self.mean=[math.fsum(r['values'][k] for r in self.rows)/len(self.rows) for k in self.metrics]
        matrix=[basis(self.coordinates(r['parameters'])) for r in self.rows]
        targets=[[r['values'][k]-center for k,center in zip(self.metrics,self.mean)] for r in self.rows]
        for j in range(self.columns):
            matrix.append([math.sqrt(RIDGE) if j==i and j!=0 else 0. for i in range(self.columns)])
            targets.append([0.]*len(self.metrics))
        self.coefficients=least_squares(matrix,targets)

    def coordinates(self,p):
        return [2*(p[k]-low)/span-1 for k,low,span in zip(KEYS,self.lower,self.span)]

    def predict(self,p):
        if self.coefficients is None:return None
        row=basis(self.coordinates(p))
        return {key:center+math.fsum(x*c[j] for x,c in zip(row,self.coefficients))
                for j,(key,center) in enumerate(zip(self.metrics,self.mean))}
