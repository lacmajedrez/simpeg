from SimPEG import np, sp, Utils, Solver
import matplotlib.pyplot as plt
import matplotlib

class ZCurve(object):
    """
        The Z-order curve is generated by interleaving the bits of an offset.

        See:

            https://github.com/cortesi/scurve
            Aldo Cortesi <aldo@corte.si>

    """
    def __init__(self, dimension, bits):
        """
            dimension: Number of dimensions
            bits: The number of bits per co-ordinate. Total number of points is
            2**(bits*dimension).
        """
        self.dimension, self.bits = dimension, bits

    def bitrange(self, x, width, start, end):
        """
            Extract a bit range as an integer.
            (start, end) is inclusive lower bound, exclusive upper bound.
        """
        return x >> (width-end) & ((2**(end-start))-1)

    def index(self, p):
        p.reverse()
        idx = 0
        iwidth = self.bits * self.dimension
        for i in range(iwidth):
            bitoff = self.bits-(i/self.dimension)-1
            poff = self.dimension-(i%self.dimension)-1
            b = self.bitrange(p[poff], self.bits, bitoff, bitoff+1) << i
            idx |= b
        return idx

    def point(self, idx):
        p = [0]*self.dimension
        iwidth = self.bits * self.dimension
        for i in range(iwidth):
            b = self.bitrange(idx, iwidth, i, i+1) << (iwidth-i-1)/self.dimension
            p[i%self.dimension] |= b
        p.reverse()
        return p

def SortGrid(grid, offset=0):
    """
        Sorts a grid by the x0 location.
    """

    eps = 1e-7
    def mycmp(c1,c2):
        c1 = grid[c1-offset]
        c2 = grid[c2-offset]
        if c1.size == 2:
            if np.abs(c1[1] - c2[1]) < eps:
                return c1[0] - c2[0]
            return c1[1] - c2[1]
        elif c1.size == 3:
            if np.abs(c1[2] - c2[2]) < eps:
                if np.abs(c1[1] - c2[1]) < eps:
                    return c1[0] - c2[0]
                return c1[1] - c2[1]
            return c1[2] - c2[2]

    class K(object):
        def __init__(self, obj, *args):
            self.obj = obj
        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0
        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0
        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0
        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0
        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0
        def __ne__(self, other):
            return mycmp(self.obj, other.obj) != 0

    return sorted(range(offset,grid.shape[0]+offset), key=K)


class Tree(object):
    def __init__(self, h_in, levels=3):
        assert type(h_in) is list, 'h_in must be a list'
        assert len(h_in) > 1, "len(h_in) must be greater than 1"

        h = range(len(h_in))
        for i, h_i in enumerate(h_in):
            if type(h_i) in [int, long, float]:
                # This gives you something over the unit cube.
                h_i = np.ones(int(h_i))/int(h_i)
            assert isinstance(h_i, np.ndarray), ("h[%i] is not a numpy array." % i)
            assert len(h_i.shape) == 1, ("h[%i] must be a 1D numpy array." % i)
            assert len(h_i) == 2**levels, "must make h and levels match"
            h[i] = h_i[:] # make a copy.
        self.h = h

        self._levels = levels
        self._levelBits = int(np.ceil(np.sqrt(levels)))+1

        self.__dirty__ = True #: The numbering is dirty!
        self._z = ZCurve(self.dim, 20)
        self._treeInds = set()
        self._treeInds.add(0)

    @property
    def levels(self): return self._levels

    @property
    def dim(self): return len(self.h)

    @property
    def nC(self): return len(self._treeInds)

    @property
    def nN(self):
        self.number()
        return self._nN

    @property
    def nF(self):
        self.number()
        return self._nF

    @property
    def nFx(self):
        self.number()
        return self._nFx

    @property
    def nFy(self):
        self.number()
        return self._nFy

    @property
    def nFz(self):
        self.number()
        return None if self.dim < 3 else self._nFz

    @property
    def nE(self):
        self.number()
        if self.dim == 2:
            return self.nF
        elif self.dim == 3:
            return len(self.edges)

    @property
    def nEx(self):
        self.number()
        if self.dim == 2:
            return self._nFy
        elif self.dim == 3:
            return self._nEx

    @property
    def nEy(self):
        self.number()
        if self.dim == 2:
            return self._nFx
        elif self.dim == 3:
            return self._nEy

    @property
    def nEz(self):
        self.number()
        return None if self.dim < 3 else self._nEz

    @property
    def vol(self):
        self.number()
        return self._vol

    @property
    def area(self):
        self.number()
        return self._area

    @property
    def edge(self):
        self.number()
        if self.dim == 2:
            return np.r_[self._area[self.nFx:], self._area[:self.nFx]]

    @property
    def _sortedInds(self):
        if getattr(self, '__sortedInds', None) is None:
            self.__sortedInds = sorted(self._treeInds)
        return self.__sortedInds

    @property
    def permuteCC(self):
        #TODO: cache these?
        P  = SortGrid(self.gridCC)
        return sp.identity(self.nC).tocsr()[P,:]

    @property
    def permuteF(self):
        #TODO: cache these?
        P = SortGrid(self.gridFx)
        P += SortGrid(self.gridFy, offset=self.nFx)
        if self.dim == 3:
            P += SortGrid(self.gridFz, offset=self.nFx+self.nFy)
        return sp.identity(self.nF).tocsr()[P,:]

    @property
    def permuteE(self):
        #TODO: cache these?
        if self.dim == 2:
            P = SortGrid(self.gridFy)
            P += SortGrid(self.gridFx, offset=self.nEx)
            return sp.identity(self.nE).tocsr()[P,:]
        if self.dim == 3:
            raise Exception()

    def _structureChange(self):
        if self.__dirty__: return

        deleteThese = ['__sortedInds', '_gridCC', '_gridFx']
        for p in deleteThese:
            if hasattr(self, p): delattr(self, p)
        self.__dirty__ = True

    def _index(self, pointer):
        assert len(pointer) is self.dim+1
        assert pointer[-1] <= self.levels
        x = self._z.index([p for p in pointer[:-1]]) # copy
        return (x << self._levelBits) + pointer[-1]

    def _pointer(self, index):
        assert type(index) in [int, long]
        n = index & (2**self._levelBits-1)
        p = self._z.point(index >> self._levelBits)
        return p + [n]

    def __contains__(self, v):
        if type(v) in [int, long]:
            return v in self._treeInds
        return self._index(v) in self._treeInds

    def refine(self, function=None, recursive=True, cells=None):

        cells = cells if cells is not None else sorted(self._treeInds)
        recurse = []
        for cell in cells:
            p = self._pointer(cell)
            do = function(self._cellC(cell)) > p[-1]
            if do:
                recurse += self._refineCell(cell)

        if recursive and len(recurse) > 0:
            self.refine(function=function, recursive=True, cells=recurse)
        return recurse

    def _refineCell(self, pointer):
        self._structureChange()
        pointer = self._asPointer(pointer)
        ind = self._asIndex(pointer)
        assert ind in self
        h = self._levelWidth(pointer[-1])/2 # halfWidth
        nL = pointer[-1] + 1 # new level
        add = lambda p:p[0]+p[1]
        added = []
        def addCell(p):
            i = self._index(p+[nL])
            self._treeInds.add(i)
            added.append(i)

        addCell(map(add, zip(pointer[:-1], [0,0,0][:self.dim])))
        addCell(map(add, zip(pointer[:-1], [h,0,0][:self.dim])))
        addCell(map(add, zip(pointer[:-1], [0,h,0][:self.dim])))
        addCell(map(add, zip(pointer[:-1], [h,h,0][:self.dim])))
        if self.dim == 3:
            addCell(map(add, zip(pointer[:-1], [0,0,h])))
            addCell(map(add, zip(pointer[:-1], [h,0,h])))
            addCell(map(add, zip(pointer[:-1], [0,h,h])))
            addCell(map(add, zip(pointer[:-1], [h,h,h])))
        self._treeInds.remove(ind)
        return added

    def _corsenCell(self, pointer):
        self._structureChange()
        raise Exception('Not yet implemented')

    def _asPointer(self, ind):
        if type(ind) in [int, long]:
            return self._pointer(ind)
        if type(ind) is list:
            return ind
        if isinstance(ind, np.ndarray):
            return ind.tolist()
        raise Exception

    def _asIndex(self, pointer):
        if type(pointer) in [int, long]:
            return pointer
        if type(pointer) is list:
            return self._index(pointer)
        raise Exception

    def _parentPointer(self, pointer):
        mod = self._levelWidth(pointer[-1]-1)
        return [p - (p % mod) for p in pointer[:-1]] + [pointer[-1]-1]

    def _cellN(self, p):
        p = self._asPointer(p)
        return [hi[:p[ii]].sum() for ii, hi in enumerate(self.h)]

    def _cellH(self, p):
        p = self._asPointer(p)
        w = self._levelWidth(p[-1])
        return [hi[p[ii]:p[ii]+w].sum() for ii, hi in enumerate(self.h)]

    def _cellC(self, p):
        return (np.array(self._cellH(p))/2.0 + self._cellN(p)).tolist()

    def _levelWidth(self, level):
        return 2**(self.levels - level)

    def _isInsideMesh(self, pointer):
        inside = True
        for p in pointer[:-1]:
            inside = inside and p >= 0 and p < 2**self.levels
        return inside

    def _getNextCell(self, ind, direction=0, positive=True):
        """
            Returns a None, int, list, or nested list
            The int is the cell number.

        """
        pointer = self._asPointer(ind)

        step = (1 if positive else -1) * self._levelWidth(pointer[-1])
        nextCell = [p if ii is not direction else p + step for ii, p in enumerate(pointer)]
        if not self._isInsideMesh(nextCell): return None

        # it might be the same size as me?
        if nextCell in self: return self._index(nextCell)
        # it might be smaller than me?
        if nextCell[-1] + 1 <= self.levels: # if I am not the smallest.
            nextCell[-1] += 1
            if not positive:
                nextCell[direction] -= step/2 # Get the closer one
            if nextCell in self: # there is at least one

                hw = self._levelWidth(pointer[-1]) / 2
                nextCell = np.array([p if ii is not direction else p + (step/2 if positive else 0) for ii, p in enumerate(pointer)])

                if self.dim == 2:
                    if direction == 0: children = [0,0,1], [0,hw,1]
                    if direction == 1: children = [0,0,1], [hw,0,1]
                elif self.dim == 3:
                    if direction == 0: children = [0,0,0,1], [0,hw,0,1], [0,0,hw,1], [0,hw,hw,1]
                    if direction == 1: children = [0,0,0,1], [hw,0,0,1], [0,0,hw,1], [hw,0,hw,1]
                    if direction == 2: children = [0,0,0,1], [hw,0,0,1], [0,hw,0,1], [hw,hw,0,1]
                nextCells = []
                for child in children:
                    nextCells.append(self._getNextCell(nextCell + child, direction=direction,positive=positive))
                return nextCells

        # it might be bigger than me?
        return self._getNextCell(self._parentPointer(pointer),
                direction=direction, positive=positive)

    @property
    def gridCC(self):
        if getattr(self, '_gridCC', None) is None:
            self._gridCC = np.zeros((len(self._treeInds),self.dim))
            for ii, ind in enumerate(self._sortedInds):
                p = self._asPointer(ind)
                self._gridCC[ii, :] = self._cellC(p)
        return self._gridCC

    @property
    def gridFx(self):
        if getattr(self, '_gridFx', None) is None:
            self.number()
        return self._gridFx

    @property
    def gridFy(self):
        if getattr(self, '_gridFy', None) is None:
            self.number()
        return self._gridFy

    @property
    def gridFz(self):
        if self.dim < 3: return None
        if getattr(self, '_gridFz', None) is None:
            self.number()
        return self._gridFz

    def _onSameLevel(self, i0, i1):
        p0 = self._asPointer(i0)
        p1 = self._asPointer(i1)
        return p0[-1] == p1[-1]

    def number(self, force=False):
        if not self.__dirty__ and not force: return

        facesX, facesY, facesZ = [], [], []
        areaX, areaY, areaZ = [], [], []
        hangingFacesX, hangingFacesY, hangingFacesZ = [], [], []
        faceXCount, faceYCount, faceZCount = -1, -1, -1
        fXm,fXp,fYm,fYp,fZm,fZp = range(6)
        vol, nodes = [], []

        def addXFace(count, p, positive=True):
            n = self._cellN(p)
            w = self._cellH(p)
            areaX.append(w[1] if self.dim == 2 else w[1]*w[2])
            if self.dim == 2:
                facesX.append([n[0] + (w[0] if positive else 0), n[1] + w[1]/2.0])
            elif self.dim == 3:
                facesX.append([n[0] + (w[0] if positive else 0), n[1] + w[1]/2.0, n[2] + w[2]/2.0])
            return count + 1
        def addYFace(count, p, positive=True):
            n = self._cellN(p)
            w = self._cellH(p)
            areaY.append(w[0] if self.dim == 2 else w[0]*w[2])
            if self.dim == 2:
                facesY.append([n[0] + w[0]/2.0, n[1] + (w[1] if positive else 0)])
            elif self.dim == 3:
                facesY.append([n[0] + w[0]/2.0, n[1] + (w[1] if positive else 0), n[2] + w[2]/2.0])
            return count + 1
        def addZFace(count, p, positive=True):
            n = self._cellN(p)
            w = self._cellH(p)
            areaZ.append(w[0]*w[1])
            facesZ.append([n[0] + w[0]/2.0, n[1] + w[1]/2.0, n[2] + (w[2] if positive else 0)])
            return count + 1

        # c2cn = dict()
        c2f = dict()
        def gc2f(ind):
            if ind in c2f: return c2f[ind]
            c2f_ind = [list() for _ in xrange(2*self.dim)]
            c2f[ind] = c2f_ind
            return c2f_ind

        def processCell(ind, faceCount, addFace, hangingFaces, DIR=0):

            fM,fP=(0,1) if DIR == 0 else (2,3) if DIR == 1 else (4,5)
            p = self._asPointer(ind)
            if self._getNextCell(p, direction=DIR, positive=False) is None:
                faceCount = addFace(faceCount, p, positive=False)
                gc2f(ind)[fM] += [faceCount]

            nextCell = self._getNextCell(p, direction=DIR)

            # Add the next Xface
            if nextCell is None:
                # on the boundary
                faceCount = addFace(faceCount, p)
                gc2f(ind)[fP] += [faceCount]
            elif type(nextCell) in [int, long] and self._onSameLevel(p,nextCell):
                # same sized cell
                faceCount = addFace(faceCount, p)
                gc2f(ind)[fP]      += [faceCount]
                gc2f(nextCell)[fM] += [faceCount]
            elif type(nextCell) in [int, long] and not self._onSameLevel(p,nextCell):
                # the cell is bigger than me
                faceCount = addFace(faceCount, p)
                gc2f(ind)[fP]      += [faceCount]
                gc2f(nextCell)[fM] += [faceCount]
                hangingFaces.append(faceCount)
            elif type(nextCell) is list:
                # the cell is smaller than me

                # TODO: ensure that things are balanced.
                p0 = self._pointer(nextCell[0])
                p1 = self._pointer(nextCell[1])

                faceCount = addFace(faceCount, p0, positive=False)
                gc2f(nextCell[0])[fM] += [faceCount]
                faceCount = addFace(faceCount, p1, positive=False)
                gc2f(nextCell[1])[fM] += [faceCount]

                gc2f(ind)[fP] += [faceCount-1,faceCount]

                hangingFaces += [faceCount-1, faceCount]

            return faceCount

        for ii, ind in enumerate(self._sortedInds):
            # c2cn[ind] = ii
            vol.append(np.prod(self._cellH(ind)))
            faceXCount = processCell(ind, faceXCount, addXFace, hangingFacesX, DIR=0)
            faceYCount = processCell(ind, faceYCount, addYFace, hangingFacesY, DIR=1)
            if self.dim == 3:
                faceZCount = processCell(ind, faceZCount, addZFace, hangingFacesZ, DIR=2)

        self._c2f = c2f
        self._area = np.array(areaX + areaY + (areaZ if self.dim == 3 else []))
        self._vol = np.array(vol)
        self._gridFx = np.array(facesX)
        self._gridFy = np.array(facesY)
        self._hangingFacesX = hangingFacesX
        self._hangingFacesY = hangingFacesY
        if self.dim == 3:
            self._gridFz = np.array(facesZ)
            self._nFz = self._gridFz.shape[0]
            self._hangingFacesZ = hangingFacesZ

        self._nC = len(self._sortedInds)
        self._nFx = self._gridFx.shape[0]
        self._nFy = self._gridFy.shape[0]
        self._nF = self._nFx + self._nFy + (self._nFz if self.dim == 3 else 0)

        self.__dirty__ = False

    @property
    def faceDiv(self):
        # print self._c2f
        if getattr(self, '_faceDiv', None) is None:
            self.number()
            # TODO: Preallocate!
            I, J, V = [], [], []
            PM = [-1,1]*self.dim # plus / minus
            offset = [0,0,self.nFx,self.nFx,self.nFx+self.nFy,self.nFx+self.nFy]

            for ii, ind in enumerate(self._sortedInds):
                faces = self._c2f[ind]
                for off, pm, face in zip(offset,PM,faces):
                    j = [_ + off for _ in face]
                    I += [ii]*len(j)
                    J += j
                    V += [pm]*len(j)

            VOL = self.vol
            D = sp.csr_matrix((V,(I,J)), shape=(self.nC, self.nF))
            S = self.area
            self._faceDiv = Utils.sdiag(1.0/VOL)*D*Utils.sdiag(S)
        return self._faceDiv

    def plotGrid(self, ax=None, showIt=False):


        axOpts = {'projection':'3d'} if self.dim == 3 else {}
        if ax is None:
            ax = plt.subplot(111, **axOpts)
        else:
            assert isinstance(ax,matplotlib.axes.Axes), "ax must be an Axes!"
            fig = ax.figure

        for ind in self._sortedInds:
            p = self._asPointer(ind)
            n = self._cellN(p)
            h = self._cellH(p)
            x = [n[0]    , n[0] + h[0], n[0] + h[0], n[0]       , n[0]]
            y = [n[1]    , n[1]       , n[1] + h[1], n[1] + h[1], n[1]]
            z = [n[2]    , n[2]       , n[2]       , n[2]       , n[2]]
            ax.plot(x,y, 'b-', zs=None if self.dim == 2 else z)

            if self.dim == 3:
                z = [n[2] + h[2], n[2] + h[2], n[2] + h[2], n[2] + h[2], n[2] + h[2]]
                ax.plot(x,y, 'b-', zs=z)
                sides = [0,0], [h[0],0], [0,h[1]], [h[0],h[1]]
                for s in sides:
                    x = [n[0] + s[0], n[0] + s[0]]
                    y = [n[1] + s[1], n[1] + s[1]]
                    z = [n[2]       , n[2] + h[2]]
                    ax.plot(x,y, 'b-', zs=z)


        ax.plot(self.gridCC[[0,-1],0], self.gridCC[[0,-1],1], 'ro', zs=None if self.dim == 2 else self.gridCC[[0,-1],2])
        ax.plot(self.gridCC[:,0], self.gridCC[:,1], 'r.', zs=None if self.dim == 2 else self.gridCC[:,2])
        ax.plot(self.gridCC[:,0], self.gridCC[:,1], 'r:', zs=None if self.dim == 2 else self.gridCC[:,2])

        # ax.plot(self.gridFx[self._hangingFacesX,0], self.gridFx[self._hangingFacesX,1], 'gs', ms=10, mfc='none', mec='green', zs=None if self.dim == 2 else self.gridFx[self._hangingFacesX,2])
        # ax.plot(self.gridFx[:,0], self.gridFx[:,1], 'g>', zs=None if self.dim == 2 else self.gridFx[:,2])
        # ax.plot(self.gridFy[self._hangingFacesY,0], self.gridFy[self._hangingFacesY,1], 'gs', ms=10, mfc='none', mec='green', zs=None if self.dim == 2 else self.gridFy[self._hangingFacesY,2])
        # ax.plot(self.gridFy[:,0], self.gridFy[:,1], 'g^', zs=None if self.dim == 2 else self.gridFy[:,2])
        if self.dim == 3:
            ax.plot(self.gridFz[self._hangingFacesZ,0], self.gridFz[self._hangingFacesZ,1], 'gs', ms=10, mfc='none', mec='green', zs=self.gridFz[self._hangingFacesZ,2])
            ax.plot(self.gridFz[:,0], self.gridFz[:,1], 'g^', zs=self.gridFz[:,2])

        if showIt:plt.show()



if __name__ == '__main__':


    def function(xc):
        r = xc - np.r_[0.5,0.5]
        dist = np.sqrt(r.dot(r))
        # if dist < 0.05:
        #     return 5
        if dist < 0.1:
            return 4
        if dist < 0.3:
            return 3
        if dist < 1.0:
            return 2
        else:
            return 0

    T = Tree([4,4,4],levels=2)
    T.refine(lambda xc:1)
    T._refineCell([0,0,0,1])
    T.plotGrid(showIt=True)

