# Copyright 2023 The Brax Authors.
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

"""Physics pipeline for fully articulated dynamics and collisiion."""
# pylint:disable=g-multiple-import
from brax import actuator
from brax import com
from brax import geometry
from brax import kinematics
from brax.base import Motion, System
from brax.positional import collisions
from brax.positional import integrator
from brax.positional import joints
from brax.positional.base import State
from jax import numpy as jp


def init(
    sys: System, q: jp.ndarray, qd: jp.ndarray, debug: bool = False
) -> State:
  """Initializes physics state.

  Args:
    sys: a brax system
    q: (q_size,) joint angle vector
    qd: (qd_size,) joint velocity vector
    debug: if True, adds contact to the state for debugging

  Returns:
    state: initial physics state
  """
  # position/velocity level terms
  x, xd = kinematics.forward(sys, q, qd)
  j, jd, a_p, a_c = kinematics.world_to_joint(sys, x, xd)
  x_i, xd_i = com.from_world(sys, x, xd)
  contact = geometry.contact(sys, x) if debug else None

  return State(q, qd, x, xd, contact, x_i, xd_i, j, jd, a_p, a_c)


def step(
    sys: System, state: State, act: jp.ndarray, debug: bool = False
) -> State:
  """Performs a single physics step using position-based dynamics.

  Resolves actuator forces, joints, and forces at acceleration level, and
  resolves collisions at velocity level with baumgarte stabilization.

  Args:
    sys: system defining the kinematic tree and other properties
    state: physics state prior to step
    act: (act_size,) actuator input vector
    debug: if True, adds contact to the state for debugging

  Returns:
    x: updated link transform in world frame
    xd: updated link motion in world frame
  """
  x_i_prev = state.x_i

  # calculate acceleration level updates
  tau = actuator.to_tau(sys, act, state.q, state.qd)
  xdd_i = Motion.create(vel=sys.gravity)
  xdd_i += joints.acceleration_update(sys, state, tau)

  # semi-implicit euler: apply acceleration update before resolving collisions
  x_i, xd_i = integrator.integrate_xdd(sys, state.x_i, state.xd_i, xdd_i)
  x, xd = com.to_world(sys, x_i, xd_i)
  state = state.replace(x=x, xd=xd, x_i=x_i, xd_i=xd_i)

  # perform position level joint updates
  x_i = joints.position_update(sys, state)
  x, _ = com.to_world(sys, x_i, xd_i)
  state = state.replace(x=x, x_i=x_i)

  # apply position level collision updates
  contact = geometry.contact(sys, x)
  x_i, dlambda = collisions.resolve_position(sys, state, x_i_prev, contact)
  xd_i_prev = xd_i

  xd_i = integrator.project_xd(sys, x_i, x_i_prev)
  x, xd = com.to_world(sys, x_i, xd_i)
  state = state.replace(x=x, xd=xd, x_i=x_i, xd_i=xd_i)

  # apply velocity level collision updates
  xdv_i = collisions.resolve_velocity(sys, state, xd_i_prev, contact, dlambda)
  xd_i = integrator.integrate_xdv(sys, xd_i, xdv_i)

  x, xd = com.to_world(sys, x_i, xd_i)
  j, jd, a_p, a_c = kinematics.world_to_joint(sys, x, xd)
  q, qd = kinematics.inverse(sys, j, jd)
  contact = geometry.contact(sys, x) if debug else None

  return State(q, qd, x, xd, contact, x_i, xd_i, j, jd, a_p, a_c)

