#   Copyright 2019 Quantum Simulation Technologies Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import copy
from typing import Tuple, Dict, List, Optional

import numpy
from scipy.special import binom

from fqe.bitstring import integer_index, get_bit, count_bits_above, set_bit, unset_bit, show_bits
from fqe.util import init_bitstring_groundstate, rand_wfn
from fqe.util import invert_bitstring_with_mask, validate_config
from fqe.fci_graph import FciGraph
from fqe.fci_graph_set import FciGraphSet


class FqeDataSet:
    """This is a basic data structure for use in the FQE.
    """

    def __init__(self, nele: int, norb: int, data: Dict[Tuple[int, int], 'FqeData']) -> None:
        self._nele = nele
        self._norb = norb
        self._data = data

        graphset = FciGraphSet(0, 1)
        for key, data in self._data.items():
            graphset.append(data.get_fcigraph()) 


    def ax_plus_y(self, factor: complex, other: 'FqeDataSet') -> None:
        """
        Performs self = a * other + self. The result will be kept inplace.
        """
        if self._data.keys() != other._data.keys():
            raise ValueError('keys are inconsistent in FqeDataSet.ax_plus_y')
        for key, sector in self._data.items():
            sector.ax_plus_y(factor, other._data[key])


    def scale(self, factor: complex) -> None:
        """
        Scales all of the data by the factor specified
        """
        for _, sector in self._data.items():
            sector.scale(factor)


    def fill(self, value: complex) -> None:
        """
        Fills all of the data to the value specified
        """
        for _, sector in self._data.items():
            sector.fill(value)


    def apply_inplace(self, array: Tuple[numpy.ndarray]) -> None:
        """
        Applies an operator specified by the tuple of numpy arrays.
        The result will be kept in-place.
        """
        other = self.apply(array)
        for key, sector in self._data.items():
            sector.coeff[:,:] = other._data[key].coeff[:,:]


    def apply(self, array: Tuple[numpy.ndarray]) -> 'FqeDataSet':
        """
        Applies an operator specified by the tuple of numpy arrays.
        The result will be returned as a FqeDataSet object. self is unchanged.
        """
        norb = self._norb
        if len(array) == 1:
            out = self.apply1(array[0])
        elif len(array) == 2:
            out = self.apply12(array[0], array[1])
        elif len(array) == 3:
            out = self.apply123(array[0], array[1], array[2])
        elif len(array) == 4:
            out = self.apply1234(array[0], array[1], array[2], array[3])
        else:
            raise ValueError('unexpected array passed in FqeData apply_inplace')
        return out


    def apply1(self, h1e: numpy.ndarray) -> 'FqeDataSet':
        """
        Applies a one-body operator specified by the tuple of numpy arrays.
        The result will be returned as a FqeDataSet object. self is unchanged.
        """
        norb = self._norb
        assert h1e.shape == (norb*2, norb*2)

        # next make FciGraphSet
        dvec = self.calculate_dvec()
        out = copy.deepcopy(self)
        for key, sector in out._data.items():
            sector.coeff = numpy.einsum('ij,ijkl->kl', h1e, dvec[key])
        return out


    def apply12(self, h1e: numpy.ndarray, h2e: numpy.ndarray) -> 'FqeDataSet':
        """
        Applies a one- and two-body operator specified by the tuple of numpy arrays.
        The result will be returned as a FqeDataSet object. self is unchanged.
        """
        norb = self._norb
        assert h1e.shape == (norb*2, norb*2) and h2e.shape == (norb*2, norb*2, norb*2, norb*2)

        h1e = copy.deepcopy(h1e)
        h2e = numpy.moveaxis(copy.deepcopy(h2e), 1, 2) * (-1.0)
        for k in range(norb*2):
            h1e[:, :] -= h2e[:, k, k, :]

        dvec = self.calculate_dvec()
        out = copy.deepcopy(self)
        for key, sector in out._data.items():
            sector.coeff = numpy.einsum('ij,ijkl->kl', h1e, dvec[key])

        for key, sector in dvec.items():
            dvec[key] = numpy.einsum('ijkl,klmn->ijmn', h2e, sector)

        result = self.calculate_coeff_with_dvec(dvec)
        for key, sector in out._data.items():
            sector.coeff += result[key]
        return out


    def apply123(self, h1e: numpy.ndarray, h2e: numpy.ndarray, h3e: numpy.ndarray) -> 'FqeDataSet':
        """
        Applies a one-, two-, and three-body operator specified by the tuple of numpy arrays.
        The result will be returned as a FqeDataSet object. self is unchanged.
        """
        norb = self._norb
        assert h3e.shape == (norb*2, norb*2, norb*2, norb*2, norb*2, norb*2)

        nh1e = numpy.copy(h1e)
        nh2e = numpy.copy(h2e)

        for i in range(norb*2):
            for j in range(norb*2):
                for k in range(norb*2):
                    nh2e[j,k,:,:] += - h3e[k,j,i,i,:,:] - h3e[j,i,k,i,:,:] - h3e[j,k,i,:,i,:]
                nh1e[:,:] += h3e[:,i,j,i,j,:]

        out = self.apply12(nh1e, nh2e)

        dvec = self.calculate_dvec()
        evec = self.calculate_evec(dvec)

        for key, sector in evec.items():
            dvec[key] = numpy.einsum('ikmjln,klmnxy->ijxy', h3e, sector)

        result = self.calculate_coeff_with_dvec(dvec)
        for key, sector in out._data.items():
            sector.coeff -= result[key]
        return out
        

    def apply1234(self, h1e: numpy.ndarray, h2e: numpy.ndarray, h3e: numpy.ndarray, h4e: numpy.ndarray) -> 'FqeDataSet':
        """
        Applies a one-, two-, three-, and four-body operator specified by the tuple of numpy arrays.
        The result will be returned as a FqeDataSet object. self is unchanged.
        """
        norb = self._norb
        assert h4e.shape == (norb*2, norb*2, norb*2, norb*2, norb*2, norb*2, norb*2, norb*2)

        nh1e = numpy.copy(h1e)
        nh2e = numpy.copy(h2e)
        nh3e = numpy.copy(h3e)

        for i in range(norb*2):
            for j in range(norb*2):
                for k in range(norb*2):
                    nh1e[:,:] -= h4e[:,j,i,k,j,i,k,:]
                    for l in range(norb*2):
                        nh2e[i,j,:,:] += h4e[j,l,i,k,l,k,:,:] + h4e[i,j,l,k,l,k,:,:] + h4e[i,l,k,j,l,k,:,:] + h4e[j,i,k,l,l,k,:,:] \
                                       + h4e[i,k,j,l,k,:,l,:] + h4e[j,i,k,l,k,:,l,:] + h4e[i,j,k,l,:,k,l,:]
                        nh3e[i,j,k,:,:,:] += h4e[k,i,j,l,l,:,:,:] + h4e[j,i,l,k,l,:,:,:] + h4e[i,l,j,k,l,:,:,:] \
                                           + h4e[i,k,j,l,:,l,:,:] + h4e[i,j,l,k,:,l,:,:] + h4e[i,j,k,l,:,:,l,:] 

        out = self.apply123(nh1e, nh2e, nh3e)

        dvec = self.calculate_dvec()
        evec = self.calculate_evec(dvec)

        for key, sector in evec.items():
            evec[key] = numpy.einsum('ikmojlnp,mnopxy->ijklxy', h4e, sector)

        dvec2 = copy.deepcopy(dvec)
        for i in range(norb*2):
            for j in range(norb*2):
                for key in evec.keys():
                    dvec2[key][:, :, :, :] = evec[key][i, j, :, :, :, :]

                cvec = self.calculate_coeff_with_dvec(dvec2)

                for key in dvec.keys():
                    dvec[key][i, j, :, :] = cvec[key][:, :]

        result = self.calculate_coeff_with_dvec(dvec)
        for key, sector in out._data.items():
            sector.coeff += result[key]
        return out


    def apply_individual_nbody(self, coeff: complex, daga: List[int], undaga: List[int], dagb: List[int], undagb: List[int]) -> 'FqeDataSet':
        """
        Apply function with an individual operator represented in arrays,
        which can handle spin-nonconserving operators and returns the result
        """
        out = copy.deepcopy(self)
        out.apply_inplace_individual_nbody(coeff, daga, undaga, dagb, undagb)
        return out


    def apply_inplace_individual_nbody(self, coeff: complex, daga: List[int], undaga: List[int], dagb: List[int], undagb: List[int]) -> None:
        """
        Apply function with an individual operator represented in arrays,
        which can handle spin-nonconserving operators 
        """
        assert len(daga) +  len(dagb) == len(undaga) + len(undagb)
        nda = len(daga) - len(undaga)

        original = copy.deepcopy(self)
        self.fill(0.0)

        for (nalpha, nbeta), source in original._data.items():
            if (nalpha+nda, nbeta-nda) in self._data.keys():
                target = self._data[(nalpha+nda, nbeta-nda)]

                alphamap = []
                betamap = []
                def make_mapping_each(alpha: bool) -> None:
                    (dag, undag) = (daga, undaga) if alpha else (dagb, undagb)
                    for index in range(source.lena() if alpha else source.lenb()):
                        current = source._core.string_alpha(index) if alpha else source._core.string_beta(index)
                        check = True
                        for i in undag:
                            if not check: break
                            check &= bool(get_bit(current, i))
                        for i in dag:
                            if not check: break
                            check &= i in undag or not bool(get_bit(current, i))
                        if check:
                            parity = 0
                            for i in reversed(undag):
                                parity += count_bits_above(current, i)
                                current = unset_bit(current, i)
                            for i in reversed(dag):
                                parity += count_bits_above(current, i)
                                current = set_bit(current, i)
                            if alpha: 
                                alphamap.append((index, target._core.index_alpha(current), (-1)**parity))
                            else:
                                betamap.append((index, target._core.index_beta(current), (-1)**parity))
                make_mapping_each(True)
                make_mapping_each(False)

                for sourcea, targeta, paritya in alphamap:
                    paritya *= (-1)**((len(dagb)+len(undagb))*nalpha) 
                    for sourceb, targetb, parityb in betamap:
                        target.coeff[targeta, targetb] = coeff * source.coeff[sourcea, sourceb] * paritya * parityb


    def evolve_inplace_individual_nbody(self, time: float, coeff: complex, daga: List[int], undaga: List[int], dagb: List[int], undagb: List[int]) -> None:
        """
        This code time-evolves a wave function with an individual n-body generator
        which is spin-nonconserving. It is assumed that hat{T}^2 = 0.
        Using TT = 0 and TTd is diagonal in the determinant space, one could evaluate as
            exp(-i(T+Td)t) = 1 + i(T+Td)t - (TTd + TdT)t^2/2 - i(TTdT + TdTTd)t^3/6 + ...
                           = -1 + cos(sqrt(TTd)) + cos(sqrt(TdT)) - iT*sin(sqrt(TdT))/sqrt(TdT) - iTd*sin(sqrt(TTd))/sqrt(TTd)
        """
        def isolate_number_operators(dag: List[int], undag: List[int], dagwork: List[int], undagwork: List[int], number: List[int]):
            """
            Pair-up daggered and undaggered operators that correspond to the same spin-orbital and isolate them,
            because they have to be treated differently.
            """
            par = 0
            for i in range(len(dag)):
                current = dag[i]
                if current in undag:
                    index1 = dagwork.index(current)
                    index2 = undagwork.index(current)
                    par += len(dagwork)-(index1+1) + index2
                    dagwork.remove(current)
                    undagwork.remove(current)
                    number.append(current)
            return par

        dagworka = copy.deepcopy(daga)
        dagworkb = copy.deepcopy(dagb)
        undagworka = copy.deepcopy(undaga)
        undagworkb = copy.deepcopy(undagb)
        numbera = [] 
        numberb = [] 

        parity = 0
        parity += isolate_number_operators(daga, undaga, dagworka, undagworka, numbera)
        parity += isolate_number_operators(dagb, undagb, dagworkb, undagworkb, numberb)
        ncoeff = coeff * (-1)**parity

        cosdata1 = copy.deepcopy(self)
        cosdata2 = copy.deepcopy(self)
        sindata1 = copy.deepcopy(self)
        sindata2 = copy.deepcopy(self)
        for key, sector in self._data.items():
            (tmp1, tmp2) = sector.apply_cos_sin(time, ncoeff, numbera + dagworka, undagworka, numberb + dagworkb, undagworkb)
            cosdata1._data[key] = tmp1
            sindata1._data[key] = tmp2
            (tmp1, tmp2) = sector.apply_cos_sin(time, ncoeff, numbera + undagworka, dagworka, numberb + undagworkb, dagworkb)
            cosdata2._data[key] = tmp1
            sindata2._data[key] = tmp2

        phase = (-1)**((len(daga)+len(undaga)) * (len(dagb)+len(undagb)) + \
                       len(daga)*(len(daga)-1)//2 + len(dagb)*(len(dagb)-1)//2 + len(undaga)*(len(undaga)-1)//2 + len(undagb)*(len(undagb)-1)//2)

        cosdata1.ax_plus_y(-1.0j, sindata1.apply_individual_nbody(numpy.conj(coeff)*phase, undaga, daga, undagb, dagb))
        cosdata2.ax_plus_y(-1.0j, sindata2.apply_individual_nbody(coeff, daga, undaga, dagb, undagb))

        self.scale(-1.0)
        self.ax_plus_y(1.0, cosdata1)
        self.ax_plus_y(1.0, cosdata2)


    def rdm1(self, bra: 'FqeDataSet' = None) -> Tuple[numpy.ndarray]:
        """
        Computes 1-particle RDMs. When bra is specified, it computes a transition RDM
        """
        assert bra is None or self._data.keys() == bra._data.keys()

        dvec2 = self.calculate_dvec() if bra is None else bra.calculate_dvec()
        out = None
        for key, sector in self._data.items():
            tmp = numpy.einsum('jikl,kl->ij', dvec2[key].conj(), self._data[key].coeff)
            out = tmp if out is None else (out + tmp)
        return (out, )


    def rdm12(self, bra: 'FqeDataSet' = None) -> Tuple[numpy.ndarray, numpy.ndarray]:
        """
        Computes 1- and 2-particle RDMs. When bra is specified, it computes a transition RDMs
        """
        assert bra is None or self._data.keys() == bra._data.keys()

        dvec = self.calculate_dvec()
        dvec2 = dvec if bra is None else bra.calculate_dvec()

        out1 = out2 = None
        for key, sector in self._data.items():
            tmp1 = numpy.einsum('jikl,kl->ij', dvec2[key].conj(), self._data[key].coeff)
            tmp2 = numpy.einsum('jikl,mnkl->imjn', dvec2[key].conj(), dvec[key]) * (-1.0)
            out1 = tmp1 if out1 is None else (out1 + tmp1)
            out2 = tmp2 if out2 is None else (out2 + tmp2)
        for i in range(out1.shape[0]):
            out2[:, i, i, :] += out1[:, :]
        return (out1, out2)


    def rdm123(self, bra: 'FqeDataSet' = None, dvec: numpy.ndarray = None, dvec2: numpy.ndarray = None, evec2: numpy.ndarray = None) -> Tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
        """
        Computes 1-, 2-, and 3-particle RDMs. When bra is specified, it computes a transition RDMs
        """
        assert bra is None or self._data.keys() == bra._data.keys()

        if dvec is None:
            dvec = self.calculate_dvec()
        if dvec2 is None:
            dvec2 = dvec if bra is None else bra.calculate_dvec()
        if evec2 is None:
            evec2 = self.calculate_evec(dvec2)

        out1 = out2 = out3 = None
        for key, sector in self._data.items():
            tmp1 = numpy.einsum('jikl,kl->ij', dvec2[key].conj(), self._data[key].coeff)
            tmp2 = numpy.einsum('jikl,mnkl->imjn', dvec2[key].conj(), dvec[key]) * (-1.0)
            tmp3 = numpy.einsum('lkjimn,opmn->ikojlp', evec2[key].conj(), dvec[key]) * (-1.0)
            out1 = tmp1 if out1 is None else (out1 + tmp1)
            out2 = tmp2 if out2 is None else (out2 + tmp2)
            out3 = tmp3 if out3 is None else (out3 + tmp3)
        nsize = out1.shape[0]
        for i in range(nsize):
            out2[:, i, i, :] += out1[:, :]
        for i in range(nsize):
            out3[:,i,:,i,:,:] -= out2[:,:,:,:]
            out3[:,:,i,:,i,:] -= out2[:,:,:,:]
            for j in range(nsize):
                out3[:,i,j,i,j,:] += out1[:,:]
                for k in range(nsize):
                    out3[j,k,i,i,:,:] -= out2[k,j,:,:]
        return (out1, out2, out3)


    def rdm1234(self, bra: 'FqeDataSet' = None) -> Tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray, numpy.ndarray]:
        """
        Computes 1-, 2-, 3-, and 4-particle RDMs. When bra is specified, it computes a transition RDMs
        """
        assert bra is None or self._data.keys() == bra._data.keys()

        dvec = self.calculate_dvec()
        dvec2 = dvec if bra is None else bra.calculate_dvec()
        evec = self.calculate_evec(dvec)
        evec2 = evec if bra is None else bra.calculate_evec(dvec2)

        (out1, out2, out3) = self.rdm123(bra, dvec, dvec2, evec2)

        out4 = None
        for key, sector in self._data.items():
            tmp4 = numpy.einsum('lkjimn,opxymn->ikoxjlpy', evec2[key].conj(), evec[key])
            out4 = tmp4 if out4 is None else (out4 + tmp4)

        nsize = out1.shape[0]
        for i in range(nsize):
            for j in range(nsize):
                for k in range(nsize):
                    out4[:,j,i,k,j,i,k,:] -= out1[:,:]
                    for l in range(nsize):
                        out4[j,l,i,k,l,k,:,:] += out2[i,j,:,:]
                        out4[i,j,l,k,l,k,:,:] += out2[i,j,:,:]
                        out4[i,l,k,j,l,k,:,:] += out2[i,j,:,:]
                        out4[j,i,k,l,l,k,:,:] += out2[i,j,:,:]
                        out4[i,k,j,l,k,:,l,:] += out2[i,j,:,:]
                        out4[j,i,k,l,k,:,l,:] += out2[i,j,:,:]
                        out4[i,j,k,l,:,k,l,:] += out2[i,j,:,:]
                        out4[k,i,j,l,l,:,:,:] += out3[i,j,k,:,:,:]
                        out4[j,i,l,k,l,:,:,:] += out3[i,j,k,:,:,:]
                        out4[i,l,j,k,l,:,:,:] += out3[i,j,k,:,:,:]
                        out4[i,k,j,l,:,l,:,:] += out3[i,j,k,:,:,:]
                        out4[i,j,l,k,:,l,:,:] += out3[i,j,k,:,:,:]
                        out4[i,j,k,l,:,:,l,:] += out3[i,j,k,:,:,:]
        return (out1, out2, out3, out4)


    def calculate_dvec(self) -> Dict[Tuple[int,int], numpy.ndarray]:
        """Generate
            D^{J}_{i,j} = sum_I <J|a^i a_j|I> C_I
        using self.coeff as C_I.
        """
        return self.calculate_dvec_with_coeff(self._data)


    def calculate_evec(self, dvec: Dict[Tuple[int,int], numpy.ndarray]) -> Dict[Tuple[int,int], numpy.ndarray]:
        """Generate
            E^{J}_{k,l,i,j} = sum_I <J|a^k a_l|I> D^{I}_{i,j}
        """
        norb = self._norb
        evec = {}
        for key, sector in dvec.items():
            evec[key] = numpy.zeros((norb*2, norb*2, norb*2, norb*2, sector.shape[2], sector.shape[3]), dtype=sector.dtype)

        civec = copy.deepcopy(self._data) 
        for i in range(norb*2):
            for j in range(norb*2):
                for key in dvec.keys():
                    civec[key].coeff[:, :] = dvec[key][i, j, :, :]

                dvec2 = self.calculate_dvec_with_coeff(civec)

                for key in evec.keys():
                    evec[key][:, :, i, j, :, :] = dvec2[key][:, :, :, :]
        return evec


    def calculate_dvec_with_coeff(self, data: Dict[Tuple[int, int], 'FqeData']) -> Dict[Tuple[int,int], numpy.ndarray]:
        """Generate
            D^{J}_{i,j} = sum_I <J|a^i a_j|I> C_I
        """
        norb = self._norb

        dvec = {}
        for key, sector in data.items():
            dvec[key] = numpy.zeros((norb*2, norb*2, sector.lena(), sector.lenb()), dtype=sector.coeff.dtype)

        for (nalpha, nbeta), sector in data.items():
            dvec0 = dvec[(nalpha, nbeta)]
            for i in range(norb):
                for j in range(norb):
                    # a^+ a |0>
                    if nalpha > 0:
                        for source, target, parity in sector.alpha_map(i, j):
                            dvec0[i, j, target, :] += sector.coeff[source, :] * parity
                    # b^+ b |0>
                    if nbeta > 0:
                        for source, target, parity in sector.beta_map(i, j):
                            dvec0[i+norb, j+norb, :, target] += sector.coeff[:, source] * parity
                    # a^+ b |0>
                    if nalpha+1 <= norb and nbeta-1 >= 0:
                        dvec1 = dvec[(nalpha+1, nbeta-1)] 
                        (alphamap, betamap) = sector.get_fcigraph().find_mapping(1, -1)
                        for sourcea, targeta, paritya in alphamap[(i,)]:
                            paritya *= (-1)**nalpha
                            for sourceb, targetb, parityb in betamap[(j,)]:
                                dvec1[i, j+norb, targeta, targetb] += sector.coeff[sourcea, sourceb] * paritya * parityb
                    # b^+ a |0>
                    if nalpha-1 >= 0 and nbeta+1 <= norb:
                        dvec1 = dvec[(nalpha-1, nbeta+1)] 
                        (alphamap, betamap) = sector.get_fcigraph().find_mapping(-1, 1)
                        for sourcea, targeta, paritya in alphamap[(j,)]:
                            paritya *= (-1)**(nalpha-1)
                            for sourceb, targetb, parityb in betamap[(i,)]:
                                dvec1[i+norb, j, targeta, targetb] += sector.coeff[sourcea, sourceb] * paritya * parityb
        return dvec


    def calculate_coeff_with_dvec(self, dvec: Dict[Tuple[int,int], numpy.ndarray]) -> Dict[Tuple[int, int], numpy.ndarray]:
        """Generate
            C_I = sum_J <I|a^i a_j|J>D^{J}_ij
        """
        norb = self._norb

        out = {}
        for key, sector in self._data.items():
            out[key] = numpy.zeros((sector.lena(), sector.lenb()), dtype=sector.coeff.dtype)

        for (nalpha, nbeta), sector in self._data.items():
            assert (nalpha, nbeta) in out.keys()
            dvec0 = dvec[(nalpha, nbeta)]
            out0 = out[(nalpha, nbeta)]
            for i in range(norb):
                for j in range(norb):
                    # <0| a^+ a |dvec>
                    if nalpha > 0:
                        for source, target, parity in sector.alpha_map(j, i):
                            out0[source, :] += dvec0[i, j, target, :] * parity
                    # <0| b^+ b |dvec>
                    if nbeta > 0:
                        for source, target, parity in sector.beta_map(j, i):
                            out0[:, source] += dvec0[i+norb, j+norb, :, target] * parity
                    # <0| b^+ a |dvec>
                    if nalpha+1 <= norb and nbeta-1 >= 0:
                        dvec1 = dvec[(nalpha+1, nbeta-1)] 
                        (alphamap, betamap) = sector.get_fcigraph().find_mapping(1, -1)
                        for sourcea, targeta, paritya in alphamap[(j,)]:
                            paritya *= (-1)**nalpha
                            for sourceb, targetb, parityb in betamap[(i,)]:
                                out0[sourcea, sourceb] += dvec1[i+norb, j, targeta, targetb] * paritya * parityb
                    # <0| a^+ b | dvec>
                    if nalpha-1 >= 0 and nbeta+1 <= norb:
                        dvec1 = dvec[(nalpha-1, nbeta+1)] 
                        (alphamap, betamap) = sector.get_fcigraph().find_mapping(-1, 1)
                        for sourcea, targeta, paritya in alphamap[(i,)]:
                            paritya *= (-1)**(nalpha-1)
                            for sourceb, targetb, parityb in betamap[(j,)]:
                                out0[sourcea, sourceb] += dvec1[i, j+norb, targeta, targetb] * paritya * parityb
        return out