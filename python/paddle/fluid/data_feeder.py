#   Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

from . import core
import numpy
import os
import six
from six.moves import zip, range, xrange
import multiprocessing
import warnings

from .framework import Variable, default_main_program, _current_expected_place
from .framework import _cpu_num, _cuda_ids
__all__ = ['DataFeeder']


def convert_dtype(dtype):
    if isinstance(dtype, core.VarDesc.VarType):
        if dtype == core.VarDesc.VarType.BOOL:
            return 'bool'
        elif dtype == core.VarDesc.VarType.FP16:
            return 'float16'
        elif dtype == core.VarDesc.VarType.FP32:
            return 'float32'
        elif dtype == core.VarDesc.VarType.FP64:
            return 'float64'
        elif dtype == core.VarDesc.VarType.INT8:
            return 'int8'
        elif dtype == core.VarDesc.VarType.INT16:
            return 'int16'
        elif dtype == core.VarDesc.VarType.INT32:
            return 'int32'
        elif dtype == core.VarDesc.VarType.INT64:
            return 'int64'
        elif dtype == core.VarDesc.VarType.UINT8:
            return 'uint8'
    else:
        if dtype in [
                'bool', 'float16', 'float32', 'float64', 'int8', 'int16',
                'int32', 'int64', 'uint8', u'bool', u'float16', u'float32',
                u'float64', u'int8', u'int16', u'int32', u'int64', u'uint8'
        ]:
            # this code is a little bit dangerous, since error could happen
            # when casting no-asci code to str in python2.
            # but since the set itself is limited, so currently, it is good.
            # however, jointly supporting python2 and python3, (as well as python4 maybe)
            # may still be a long-lasting problem.
            return str(dtype)

    raise ValueError(
        "dtype must be any of [bool, float16, float32, float64, int8, int16, "
        "int32, int64, uint8]")


def check_type_and_dtype(input,
                         input_name,
                         expected_type,
                         expected_dtype,
                         op_name,
                         extra_message=''):
    check_type(input, input_name, expected_type, op_name, extra_message)
    check_dtype(input.dtype, input_name, expected_dtype, op_name, extra_message)


def check_type(input, input_name, expected_type, op_name, extra_message=''):
    if not isinstance(input, expected_type):
        raise TypeError(
            "The type of '%s' in %s must be %s, but received %s. %s" %
            (input_name, op_name, expected_type, type(input), extra_message))


def check_dtype(input_dtype,
                input_name,
                expected_dtype,
                op_name,
                extra_message=''):
    if convert_dtype(input_dtype) in ['float16']:
        warnings.warn(
            "The data type of '%s' in %s only support float16 in GPU now. %s" %
            (input_name, op_name, extra_message))
    if convert_dtype(input_dtype) not in expected_dtype:
        raise TypeError(
            "The data type of '%s' in %s must be %s, but received %s. %s" %
            (input_name, op_name, expected_dtype, convert_dtype(input_dtype),
             extra_message))


class DataToLoDTensorConverter(object):
    def __init__(self, place, lod_level, shape, dtype):
        self.place = place
        self.lod_level = lod_level
        self.shape = shape
        negtive_count = 0
        for s in self.shape:
            if s < 0:
                negtive_count += 1
            if negtive_count > 1:
                self.shape = None
                break
        self.dtype = convert_dtype(dtype)
        self._reset()

    def _reset(self):
        self.data = []
        self.lod = [[] for _ in six.moves.range(self.lod_level)]

    def feed(self, data):
        self._feed_impl_(data, self.lod, self.lod_level)

    def _feed_impl_(self, data, lod, lod_level):
        if lod_level == 0:
            self.data.append(data)
        else:
            lod[0].append(len(data))
            for each_data in data:
                self._feed_impl_(each_data, lod[1:], lod_level - 1)

    def _check_shape(self, shape):
        for s1, s2 in zip(self.shape, shape):
            if s1 != s2 and s1 >= 0 and s2 >= 0:
                raise ValueError(
                    "Shape not match. What is defined in data layer is {}, but receive {}".
                    format(self.shape, shape))

    def done(self):
        arr = numpy.array(self.data, dtype=self.dtype)
        if self.shape:
            if len(arr.shape) != len(self.shape):
                try:
                    arr = arr.reshape(self.shape)
                except ValueError:
                    raise ValueError(
                        "Reshape error. What is defined in data layer is {}, but receive {}"
                        .format(self.shape, arr.shape))
        t = core.LoDTensor()
        t.set(arr, self.place)
        if self.lod_level > 0:
            t.set_recursive_sequence_lengths(self.lod)
        self._reset()
        return t


class BatchedTensorProvider(object):
    def __init__(self, feed_list, place, batch_size, generator, drop_last):
        self.place = place
        self.batch_size = batch_size
        self.generator = generator
        self.converters = []
        self.drop_last = drop_last

        for var in feed_list:
            assert var.lod_level == 0, "lod_level must be 0"
            self.converters.append(
                DataToLoDTensorConverter(
                    place=self.place,
                    lod_level=0,
                    shape=var.shape,
                    dtype=var.dtype))

    def _done(self):
        return [c.done() for c in self.converters]

    def __call__(self):
        idx = 0
        for each_sample in self.generator():
            for each_slot, each_converter in six.moves.zip(each_sample,
                                                           self.converters):
                each_converter.data.append(each_slot)

            idx += 1
            if idx == self.batch_size:
                idx = 0
                yield self._done()

        if not self.drop_last and idx > 0:
            yield self._done()
        else:
            [c._reset() for c in self.converters]


class DataFeeder(object):
    """
    DataFeeder converts the data that returned by a reader into a data
    structure that can feed into Executor. The reader is usually a 
    python generator that returns a list of mini-batch data entries. 

    Parameters:
        feed_list (list): Variables or names of Variables that need
            to feed.
        place (:ref:`api_fluid_CPUPlace` | :ref:`api_fluid_CUDAPlace` ): 
            place indicates the device (CPU | GPU) the data will be fed into, if 
            you want to feed data into GPU, please using :code:`fluid.CUDAPlace(i)` 
            (:code:`i` represents the GPU id), or if you want to feed data into CPU, 
            please using :code:`fluid.CPUPlace()`.
        program (:ref:`api_fluid_Program` , optional): The Program that will 
            feed data into, if program is None, it will use default_main_program(). 
            Default None.

    Raises:
        :code:`ValueError` - If some Variables are not in this Program.

    Example:
        ..  code-block:: python

            import numpy as np
            import paddle
            import paddle.fluid as fluid
            
            place = fluid.CPUPlace()
            def reader():
                for _ in range(4):
                    yield np.random.random([4]).astype('float32'), np.random.random([3]).astype('float32'),
            
            main_program = fluid.Program()
            startup_program = fluid.Program()
            
            with fluid.program_guard(main_program, startup_program):
                data_1 = fluid.data(name='data_1', shape=[None, 2, 2], dtype='float32')
                data_2 = fluid.data(name='data_2', shape=[None, 1, 3], dtype='float32')
                out = fluid.layers.fc(input=[data_1, data_2], size=2)
                # ...
            feeder = fluid.DataFeeder([data_1, data_2], place)
            
            exe = fluid.Executor(place)
            exe.run(startup_program)
            
            feed_data = feeder.feed(reader())
            
            # print feed_data to view feed results
            # print(feed_data['data_1'])
            # print(feed_data['data_2'])
            
            outs = exe.run(program=main_program,
                            feed=feed_data,
                            fetch_list=[out])
            print(outs)

    """

    def __init__(self, feed_list, place, program=None):
        self.feed_dtypes = []
        self.feed_names = []
        self.feed_shapes = []
        self.feed_lod_level = []
        if program is None:
            program = default_main_program()
        for each_var in feed_list:
            if isinstance(each_var, six.string_types):
                each_var = program.block(0).var(each_var)
            if not isinstance(each_var, Variable):
                raise TypeError("Feed list should contain a list of variable")
            self.feed_dtypes.append(each_var.dtype)
            self.feed_names.append(each_var.name)
            self.feed_lod_level.append(each_var.lod_level)
            self.feed_shapes.append(each_var.shape)

        self.place = place

    def feed(self, iterable):
        """
        According to :code:`feed_list` of :code:`DataFeeder` and :code:`iterable` , converts 
        the input into a data structure that can feed into Executor.

        Parameters:
            iterable (generator): user defined python generator to read the raw input data

        Returns: 
            :code:`dict`: a :code:`dict` that contains (variable name - converted tensor) pairs

        Example:
            ..  code-block:: python

                # In this example, reader - generator will return a list of ndarray of 3 elements
                # feed API will convert each ndarray input into a tensor
                # the return result is a dict with keys: data_1, data_2, data_3
                # result['data_1']  a LoD-Tensor with shape of  [5, 2, 1, 3]. 5 is batch size, and [2, 1, 3] is the real shape of data_1.
                # result['data_2'], result['data_3'] are similar.
                import numpy as np
                import paddle.fluid as fluid
                
                def reader(limit=5):
                    for i in range(1, limit + 1):
                        yield np.ones([6]).astype('float32') * i , np.ones([1]).astype('int64') * i, np.random.random([9]).astype('float32')
                
                data_1 = fluid.data(name='data_1', shape=[None, 2, 1, 3])
                data_2 = fluid.data(name='data_2', shape=[None, 1], dtype='int64')
                data_3 = fluid.data(name='data_3', shape=[None, 3, 3], dtype='float32')
                feeder = fluid.DataFeeder(['data_1','data_2', 'data_3'], fluid.CPUPlace())
                
                
                result = feeder.feed(reader())
                print(result['data_1'])
                print(result['data_2'])
                print(result['data_3'])

        """
        converter = []
        for lod_level, shape, dtype in six.moves.zip(
                self.feed_lod_level, self.feed_shapes, self.feed_dtypes):
            converter.append(
                DataToLoDTensorConverter(
                    place=self.place,
                    lod_level=lod_level,
                    shape=shape,
                    dtype=dtype))

        for each_sample in iterable:
            assert len(each_sample) == len(converter), (
                "The number of fields in data (%d) does not match " +
                "len(feed_list) (%d)") % (len(each_sample), len(converter))
            for each_converter, each_slot in six.moves.zip(converter,
                                                           each_sample):
                each_converter.feed(each_slot)
        ret_dict = {}
        for each_name, each_converter in six.moves.zip(self.feed_names,
                                                       converter):
            ret_dict[each_name] = each_converter.done()
        return ret_dict

    def feed_parallel(self, iterable, num_places=None):
        """
        Similar with feed function, feed_parallel is used with multiple devices (CPU|GPU).
        Here :code:`iterable` is a list of python generators. The data return by each 
        generator in the list will be fed into a seperate device.        

        Parameters:
            iterable (list|tuple): list of user-defined python geneators. The element 
                number should match the :code:`num_places`.
            num_places (int, optional): the number of devices. If not provided (None), 
                all available devices on the machine will be used. Default None.

        Returns: 
            :code:`generator`: a :code:`generator` that generate dict which contains (variable name - converted tensor) pairs, 
            the total number of dicts will be generated matches with the :code:`num_places`

        .. note::        
            The number of devices - :code:`num_places` should equal to the generator (element of :code:`iterable` ) number

        Example:
            ..  code-block:: python

                import numpy as np
                import paddle.fluid as fluid

                def generate_reader(batch_size, base=0, factor=1):
                    def _reader():
                        for i in range(batch_size):
                            yield np.ones([4]) * factor + base, np.ones([4]) * factor + base + 5
                    return _reader()

                x = fluid.data(name='x', shape=[None, 2, 2])
                y = fluid.data(name='y', shape=[None, 2, 2], dtype='float32')

                z = fluid.layers.elementwise_add(x, y)

                feeder = fluid.DataFeeder(['x','y'], fluid.CPUPlace())
                place_num = 2
                places = [fluid.CPUPlace() for x in range(place_num)]
                data = []
                exe = fluid.Executor(fluid.CPUPlace())
                exe.run(fluid.default_startup_program())
                program = fluid.CompiledProgram(fluid.default_main_program()).with_data_parallel(places=places)

                # print sample feed_parallel r resultt
                # for item in list(feeder.feed_parallel([generate_reader(5, 0, 1), generate_reader(3, 10, 2)], 2)):
                #     print(item['x'])
                #     print(item['y'])

                reader_list = [generate_reader(5, 0, 1), generate_reader(3, 10, 2)]
                res = exe.run(program=program, feed=list(feeder.feed_parallel(reader_list, 2)), fetch_list=[z])
                print(res)

        """
        if isinstance(self.place, core.CUDAPlace):
            places = [
                core.CUDAPlace(i)
                for i in six.moves.xrange(
                    self._get_number_of_places_(num_places))
            ]
        else:
            places = [
                core.CPUPlace()
                for _ in six.moves.xrange(
                    self._get_number_of_places_(num_places))
            ]

        if len(iterable) != len(places):
            raise ValueError("feed_parallel takes multiple mini-batches. Each "
                             "mini-batch will be feed on each device. The "
                             "number of devices and number of mini-batches "
                             "must be same.")

        place = self.place
        for p, batch in six.moves.zip(places, iterable):
            self.place = p
            yield self.feed(batch)
        self.place = place

    def _get_number_of_places_(self, num_places):
        if num_places is not None:
            return int(num_places)
        elif isinstance(self.place, core.CUDAPlace):
            return len(_cuda_ids())
        else:
            return _cpu_num()

    def decorate_reader(self,
                        reader,
                        multi_devices,
                        num_places=None,
                        drop_last=True):
        """
        Decorate the reader (generator) to fit multiple devices. The reader generate
        multiple mini-batches. Each mini-batch will be fed into a single device.

        Parameters:
            reader(generator): a user defined python generator used to get :code:`mini-batch` of data.
                A :code:`mini-batch` can be regarded as a python generator that returns batchs of input 
                entities, just like the below :code:`_mini_batch` in the code example.                      
            multi_devices(bool): indicate whether to use multiple devices or not.
            num_places(int, optional): if :code:`multi_devices` is True, you can specify the number
                of devices(CPU|GPU) to use, if multi_devices is None, the function will use all the
                devices of the current machine. Default None.
            drop_last(bool, optional): whether to drop the last round of data if it is not enough to 
                feed all devices. Default True.

        Returns: 
            :code:`generator`: a new :code:`generator` which return converted dicts that can be fed into Executor
            
        Raises:
            :code:`ValueError`: If drop_last is False and the data cannot fit devices perfectly.

        Example:
            ..  code-block:: python

                import numpy as np
                import paddle
                import paddle.fluid as fluid
                import paddle.fluid.compiler as compiler
                
                def reader():
                    def _mini_batch(batch_size):
                        for i in range(batch_size):
                            yield np.random.random([16]).astype('float32'), np.random.randint(10, size=[1])

                    for _ in range(10):
                        yield _mini_batch(np.random.randint(1, 10))
                
                place_num = 3
                places = [fluid.CPUPlace() for _ in range(place_num)]
                
                # a simple network sample
                data = fluid.data(name='data', shape=[None, 4, 4], dtype='float32')
                label = fluid.data(name='label', shape=[None, 1], dtype='int64')
                hidden = fluid.layers.fc(input=data, size=10)
                
                feeder = fluid.DataFeeder(place=places[0], feed_list=[data, label])
                reader = feeder.decorate_reader(reader, multi_devices=True, num_places=3, drop_last=True)
                
                exe = fluid.Executor(places[0])
                exe.run(fluid.default_startup_program())
                compiled_prog = compiler.CompiledProgram(
                         fluid.default_main_program()).with_data_parallel(places=places)
                
                for i,data in enumerate(reader()):
                    # print data if you like
                    # print(i, data)
                    ret = exe.run(compiled_prog, feed=data, fetch_list=[hidden])
                    print(ret)

        """

        def __reader_creator__():
            if not multi_devices:
                for item in reader():
                    yield self.feed(item)
            else:
                num = self._get_number_of_places_(num_places)
                item = []
                for batch in reader():
                    item.append(batch)
                    if len(item) == num:
                        yield list(self.feed_parallel(item, num))
                        item = []
                if not drop_last and len(item) != 0:
                    raise ValueError(
                        "The data batch which cannot fit for devices will be "
                        "dropped is not implementation. Other strategies are "
                        "not implemented")

        return __reader_creator__


class NumpyToLoDTensorConverter(object):
    def __init__(self, place):
        self.place = place
        self.data = []
        self._reset()

    def _reset(self):
        self.data = []

    def feed(self, data):
        self.data.append(data)

    def done(self):
        arr = numpy.array(self.data)
        t = core.LoDTensor()
        t.set(arr, self.place)
        self._reset()
        return t


class DygraphListTensorProvider(object):
    def __init__(self, generator, place):
        self.generator = generator
        self.converters = []
        if place:
            if isinstance(place, (core.CUDAPlace, core.CPUPlace)):
                self.place = place
            else:
                raise ValueError("Please specify a valid place values \
                    such as core.CPUPlace or core.CUDAPlace")
        else:
            self.place = _current_expected_place()

    def _read_data(self, iterable, place):
        for items in iterable:
            if len(self.converters) < len(items):
                for _ in items:
                    self.converters.append(NumpyToLoDTensorConverter(place))
            for each_converter, each_slot in six.moves.zip(self.converters,
                                                           items):
                each_converter.feed(each_slot)
        yield [c.done() for c in self.converters]

    def __call__(self):
        for batch in self.generator():
            yield list(self._read_data(batch, self.place))
