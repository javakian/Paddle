#  Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function
import unittest
import numpy as np
from op_test import OpTest
import paddle.fluid.core as core
from paddle.fluid.op import Operator
import paddle.fluid as fluid
from paddle.fluid import compiler, Program, program_guard


class ElementwiseMulOp(OpTest):
    def init_kernel_type(self):
        self.use_mkldnn = False

    def setUp(self):
        self.op_type = "elementwise_mul"
        self.dtype = np.float32
        self.axis = -1
        self.init_dtype()
        self.init_input_output()
        self.init_kernel_type()
        self.init_axis()

        self.inputs = {
            'X': OpTest.np_dtype_to_fluid_dtype(self.x),
            'Y': OpTest.np_dtype_to_fluid_dtype(self.y)
        }
        self.outputs = {'Out': self.out}
        self.attrs = {'axis': self.axis, 'use_mkldnn': self.use_mkldnn}

    def test_check_output(self):
        # TODO(wangzhongpu): support mkldnn op in dygraph mode
        self.check_output(check_dygraph=(self.use_mkldnn == False))

    def test_check_grad_normal(self):
        # TODO(wangzhongpu): support mkldnn op in dygraph mode
        self.check_grad(
            ['X', 'Y'], 'Out', check_dygraph=(self.use_mkldnn == False))

    def test_check_grad_ingore_x(self):
        # TODO(wangzhongpu): support mkldnn op in dygraph mode
        self.check_grad(
            ['Y'],
            'Out',
            no_grad_set=set("X"),
            check_dygraph=(self.use_mkldnn == False))

    def test_check_grad_ingore_y(self):
        # TODO(wangzhongpu): support mkldnn op in dygraph mode
        self.check_grad(
            ['X'],
            'Out',
            no_grad_set=set('Y'),
            check_dygraph=(self.use_mkldnn == False))

    def init_input_output(self):
        self.x = np.random.uniform(0.1, 1, [13, 17]).astype(self.dtype)
        self.y = np.random.uniform(0.1, 1, [13, 17]).astype(self.dtype)
        self.out = np.multiply(self.x, self.y)

    def init_dtype(self):
        pass

    def init_axis(self):
        pass


class TestElementwiseMulOp_scalar(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4).astype(np.float32),
            'Y': np.random.rand(1).astype(np.float32)
        }
        self.outputs = {'Out': self.inputs['X'] * self.inputs['Y']}
        self.init_kernel_type()


class TestElementwiseMulOp_Vector(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.random((32, )).astype("float64"),
            'Y': np.random.random((32, )).astype("float64")
        }
        self.outputs = {'Out': np.multiply(self.inputs['X'], self.inputs['Y'])}
        self.init_kernel_type()


class TestElementwiseMulOp_broadcast_0(ElementwiseMulOp):
    def init_input_output(self):
        self.x = np.random.rand(2, 3, 4).astype(self.dtype)
        self.y = np.random.rand(2).astype(self.dtype)
        self.out = self.x * self.y.reshape(2, 1, 1)

    def init_axis(self):
        self.axis = 0


class TestElementwiseMulOp_broadcast_1(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4).astype(np.float64),
            'Y': np.random.rand(3).astype(np.float64)
        }

        self.attrs = {'axis': 1}
        self.outputs = {
            'Out': self.inputs['X'] * self.inputs['Y'].reshape(1, 3, 1)
        }
        self.init_kernel_type()


class TestElementwiseMulOp_broadcast_2(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4).astype(np.float64),
            'Y': np.random.rand(4).astype(np.float64)
        }

        self.outputs = {
            'Out': self.inputs['X'] * self.inputs['Y'].reshape(1, 1, 4)
        }
        self.init_kernel_type()


class TestElementwiseMulOp_broadcast_3(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4, 5).astype(np.float64),
            'Y': np.random.rand(3, 4).astype(np.float64)
        }

        self.attrs = {'axis': 1}
        self.outputs = {
            'Out': self.inputs['X'] * self.inputs['Y'].reshape(1, 3, 4, 1)
        }
        self.init_kernel_type()


class TestElementwiseMulOp_broadcast_4(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4).astype(np.float64),
            'Y': np.random.rand(2, 1, 4).astype(np.float64)
        }
        self.outputs = {'Out': self.inputs['X'] * self.inputs['Y']}
        self.init_kernel_type()


class TestElementwiseMulOp_broadcast_5(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4, 5).astype(np.float64),
            'Y': np.random.rand(2, 3, 1, 5).astype(np.float64)
        }
        self.outputs = {'Out': self.inputs['X'] * self.inputs['Y']}
        self.init_kernel_type()


@unittest.skipIf(not core.is_compiled_with_cuda(),
                 "core is not compiled with CUDA")
class TestElementwiseMulOpFp16(ElementwiseMulOp):
    def init_dtype(self):
        self.dtype = np.float16


class TestElementwiseMulOp_commonuse_1(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 4).astype(np.float64),
            'Y': np.random.rand(1, 1, 4).astype(np.float64)
        }
        self.outputs = {'Out': self.inputs['X'] * self.inputs['Y']}
        self.init_kernel_type()


class TestElementwiseMulOp_commonuse_2(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(2, 3, 1, 5).astype(np.float64),
            'Y': np.random.rand(2, 1, 4, 1).astype(np.float64)
        }
        self.outputs = {'Out': self.inputs['X'] * self.inputs['Y']}
        self.init_kernel_type()


class TestElementwiseMulOp_xsize_lessthan_ysize(ElementwiseMulOp):
    def setUp(self):
        self.op_type = "elementwise_mul"
        self.inputs = {
            'X': np.random.rand(4, 5).astype(np.float64),
            'Y': np.random.rand(2, 3, 4, 5).astype(np.float64)
        }

        self.attrs = {'axis': 2}

        self.outputs = {
            'Out': self.inputs['X'].reshape(1, 1, 4, 5) * self.inputs['Y']
        }
        self.init_kernel_type()


class TestElementwiseMulOpError(unittest.TestCase):
    def test_errors(self):
        with program_guard(Program(), Program()):
            # the input of elementwise_mul must be Variable.
            x1 = fluid.create_lod_tensor(
                np.array([-1, 3, 5, 5]), [[1, 1, 1, 1]], fluid.CPUPlace())
            y1 = fluid.create_lod_tensor(
                np.array([-1, 3, 5, 5]), [[1, 1, 1, 1]], fluid.CPUPlace())
            self.assertRaises(TypeError, fluid.layers.elementwise_mul, x1, y1)

            # the input dtype of elementwise_mul must be float16 or float32 or float64 or int32 or int64
            # float16 only can be set on GPU place
            x2 = fluid.layers.data(name='x2', shape=[3, 4, 5, 6], dtype="uint8")
            y2 = fluid.layers.data(name='y2', shape=[3, 4, 5, 6], dtype="uint8")
            self.assertRaises(TypeError, fluid.layers.elementwise_mul, x2, y2)


if __name__ == '__main__':
    unittest.main()
