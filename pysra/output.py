# The MIT License (MIT)
#
# Copyright (c) 2016-2018 Albert Kottke
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import collections

import numpy as np
import scipy.integrate

from .motion import TimeSeriesMotion, WaveField, GRAVITY


class OutputCollection(collections.UserList):
    def __init__(self, *outputs):
        super().__init__(outputs)

    def __call__(self, calc, name=None):
        # Save results
        for o in self.data:
            o(calc, name=name)

    def reset(self):
        for o in self.data:
            o.reset()


def append_arrays(many, single):
    """Append an array to another padding with NaNs for constant length.

    Parameters
    ----------
    many : array_like of rank (j, k)
        values appended to a copy of this array. This may be a 1-D or 2-D
        array.
    single : array_like of rank l
        values to append. This should be a 1-D array.

    Returns
    -------
    append : :class:`numpy.ndarray`
        2-D array with rank (j + 1, max(k, l)) with missing values padded
        with :class:`numpy.nan`
    """
    assert np.ndim(single) == 1

    # Check if the values need to be padded to for equal length
    diff = single.shape[0] - many.shape[0]
    if diff < 0:
        single = np.pad(single, (0, -diff), 'constant', constant_values=np.nan)
    elif diff > 0:
        many = np.pad(many, ((0, diff), ), 'constant', constant_values=np.nan)
    else:
        # No padding needed
        pass
    return np.c_[many, single]


class Output(object):
    _const_ref = False

    def __init__(self, refs=None):
        self._refs = refs if refs is None else np.asarray(refs)
        self._values = None
        self._names = []

    def __call__(self, calc, name=None):
        if name is None:
            if self.values is None:
                i = 1
            elif len(self.values.shape) == 1:
                i = 2
            else:
                i = self.values.shape[1] + 1
            name = 'r%d' % i
        self._names.append(name)

    @property
    def refs(self):
        return self._refs

    @property
    def values(self):
        return self._values

    @property
    def names(self):
        return self._names

    def reset(self):
        self._values = None
        self._names = []
        if not self._const_ref:
            self._refs = None

    def iter_results(self):
        shared_ref = len(self.refs.shape) == 1
        for i, name in enumerate(self.names):
            refs = self.refs if shared_ref else self.refs[:, i]
            values = self.values if len(
                self.values.shape) == 1 else self.values[:, i]
            yield name, refs, values

    def _add_refs(self, refs):
        refs = np.asarray(refs)
        if self._refs is None:
            self._refs = refs
        elif len(refs) == len(self._refs) and np.allclose(refs, self._refs):
            # Same values
            pass
        else:
            # Different values
            self._refs = append_arrays(self._refs, refs)

    def _add_values(self, values):
        values = np.asarray(values)
        if self._values is None:
            self._values = values
        else:
            self._values = append_arrays(self._values, values)


class OutputLocation(object):
    def __init__(self, wave_field, depth=None, index=None):
        self._depth = depth
        self._index = index
        if not isinstance(wave_field, WaveField):
            wave_field = WaveField[wave_field]
        self._wave_field = wave_field

    @property
    def depth(self):
        return self._depth

    @property
    def index(self):
        return self._index

    @property
    def wave_field(self):
        return self._wave_field

    def __call__(self, profile):
        """Lookup the location with the profile."""
        return profile.location(
            self.wave_field, depth=self.depth, index=self.index)


class LocationBasedOutput(Output):
    def __init__(self, ref, location):
        super().__init__(ref)
        self._location = location

    @property
    def location(self):
        return self._location

    def __call__(self, calc, name=None):
        raise NotImplementedError

    def _get_location(self, calc):
        """Locate location within the profile."""
        return self._location(calc.profile)


class TimeSeriesOutput(LocationBasedOutput):
    xlabel = 'Time (sec)'
    ylabel = NotImplemented

    def __init__(self, location):
        super().__init__(None, location)

    @property
    def times(self):
        return self.refs

    def __call__(self, calc, name=None):
        if not isinstance(calc.motion, TimeSeriesMotion):
            raise NotImplementedError
        Output.__call__(self, calc, name)
        # Compute the response
        loc = self._get_location(calc)
        tf = self._get_trans_func(calc, loc)
        values = calc.motion.calc_time_series(tf)
        values = self._modify_values(calc, loc, values)
        self._add_values(values)
        # Add the reference
        refs = calc.motion.time_step * np.arange(len(values))
        self._add_refs(refs)

    def _get_trans_func(self, calc, location):
        raise NotImplementedError

    def _modify_values(self, calc, location, values):
        return values


class AccelerationTSOutput(TimeSeriesOutput):
    ylabel = 'Acceleration (g)'

    def _get_trans_func(self, calc, location):
        return calc.calc_accel_tf(calc.loc_input, location)


class AriasIntensityTSOutput(AccelerationTSOutput):
    ylabel = 'Arias Intensity (m/s)'

    def _modify_values(self, calc, location, values):
        time_step = calc.motion.time_step
        values = scipy.integrate.cumtrapz(values ** 2, dx=time_step)
        values *= GRAVITY * np.pi / 2
        return values


class StrainTSOutput(TimeSeriesOutput):
    def __init__(self, location, in_percent=False):
        super().__init__(location)
        self._in_percent = in_percent
        assert self.location.wave_field == WaveField.within

    def _get_trans_func(self, calc, location):
        return calc.calc_strain_tf(calc.loc_input, location)

    def _modify_values(self, calc, location, values):
        if self._in_percent:
            # Convert to percent
            values *= 100.
        return values

    @property
    def ylabel(self):
        suffix = '(%)' if self._in_percent else '(dec)'
        return 'Shear Strain ' + suffix


class StressTSOutput(TimeSeriesOutput):
    def __init__(self, location, damped=False, normalized=False):
        super().__init__(location)
        self._damped = damped
        self._normalized = normalized
        assert self.location.wave_field == WaveField.within

    @property
    def damped(self):
        return self._damped

    @property
    def ylabel(self):
        if self._normalized:
            ylabel = 'Stress Ratio (τ/σ`ᵥ)'
        else:
            ylabel = 'Stress (τ)'

        return ylabel

    def _get_trans_func(self, calc, location):
        tf = calc.calc_stress_tf(calc.loc_input, location, self.damped)

        if self._normalized:
            # Correct by effective stress at depth
            tf /= location.stress_vert(effective=True)

        return tf


class ResponseSpectrumOutput(LocationBasedOutput):
    _const_ref = True
    xlabel = 'Frequency (Hz)'

    def __init__(self, freqs, location, osc_damping):
        super().__init__(freqs, location)
        self._osc_damping = osc_damping

    @property
    def freqs(self):
        return self._refs

    @property
    def periods(self):
        return 1. / np.asarray(self._refs)

    @property
    def osc_damping(self):
        return self._osc_damping

    @property
    def ylabel(self):
        return f'{100 * self.osc_damping:g}%-Damped, Spec. Accel. (g)'

    def __call__(self, calc, name=None):
        Output.__call__(self, calc, name)
        loc = self._get_location(calc)
        tf = calc.calc_accel_tf(calc.loc_input, loc)
        ars = calc.motion.calc_osc_accels(self.freqs, self.osc_damping, tf)
        self._add_values(ars)


class RatioBasedOutput(Output):
    def __init__(self, refs, location_in, location_out):
        super().__init__(refs)
        self._location_in = location_in
        self._location_out = location_out

    @property
    def location_in(self):
        return self._location_in

    @property
    def location_out(self):
        return self._location_out

    def __call__(self, calc, name=None):
        raise NotImplementedError

    def _get_locations(self, calc):
        """Locate locations within the profile."""
        return (self._location_in(calc.profile),
                self._location_out(calc.profile))


class AccelTransferFunctionOutput(RatioBasedOutput):
    def __call__(self, calc, name=None):
        Output.__call__(self, calc, name)
        # Locate position within the profile
        loc_in, loc_out = self._get_locations(calc)
        # Compute the response
        tf = calc.calc_accel_tf(loc_in, loc_out)
        self._add_values(tf)
        self._add_refs(calc.motion.freqs)

    @property
    def freqs(self):
        return self._refs


class ResponseSpectrumRatioOutput(RatioBasedOutput):
    _const_ref = True
    xlabel = 'Frequency (Hz)'

    def __init__(self, freqs, location_in, location_out, osc_damping):
        super().__init__(freqs, location_in, location_out)
        self._osc_damping = osc_damping

    @property
    def freqs(self):
        return self._refs

    @property
    def periods(self):
        return 1. / np.asarray(self._refs)

    @property
    def osc_damping(self):
        return self._osc_damping

    @property
    def ylabel(self):
        return f'{100 * self.osc_damping:g}%-Damped, Resp. Spectral Ratio'

    def __call__(self, calc, name=None):
        Output.__call__(self, calc, name)
        loc_in, loc_out = self._get_locations(calc)
        in_ars = calc.motion.calc_osc_accels(self.freqs, self.osc_damping,
                                             calc.calc_accel_tf(calc.loc_input,
                                                                loc_in))
        out_ars = calc.motion.calc_osc_accels(self.freqs, self.osc_damping,
                                              calc.calc_accel_tf(
                                                  calc.loc_input, loc_out))
        ratio = out_ars / in_ars
        self._add_values(ratio)


class ProfileBasedOutput(Output):
    def __init__(self):
        super().__init__()

    def __call__(self, calc, name=None):
        Output.__call__(self, calc, name)
        depths = [0] + [l.depth_mid for l in calc.profile[:-1]]
        self._add_refs(depths)


class MaxStrainProfile(ProfileBasedOutput):
    def __init__(self):
        super().__init__()

    def __call__(self, calc, name=None):
        ProfileBasedOutput.__call__(self, calc, name)
        values = [0] + [l.strain for l in calc.profile[:-1]]
        self._add_values(values)


class CyclicStressRatioProfile(ProfileBasedOutput):
    # From Idriss and Boulanger (2008, pg. 70):
    # The 0.65 is a constant used to represent the reference stress
    # level. While being somewhat arbitrary it was selected in the
    # beginning of the development of liquefaction procedures in 1966
    # and has been in use ever since.
    _stress_level = 0.65

    def __init__(self):
        super().__init__()

    def __call__(self, calc, name=None):
        ProfileBasedOutput.__call__(self, calc, name)
        values = [
            l.stress_shear_max / l.stress_vert(l.thickness / 2, True)
            for l in calc.profile[:-1]
        ]
        # Repeat the first value for the surface
        values = self._stress_level * np.array([values[0]] + values)
        self._add_values(values)
