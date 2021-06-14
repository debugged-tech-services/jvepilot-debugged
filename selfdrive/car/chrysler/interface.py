#!/usr/bin/env python3
from cereal import car
from selfdrive.car.chrysler.values import CAR
from selfdrive.car import STD_CARGO_KG, scale_rot_inertia, scale_tire_stiffness, gen_empty_fingerprint
from selfdrive.car.interfaces import CarInterfaceBase
from selfdrive.config import Conversions as CV
from common.cached_params import CachedParams
from common.op_params import opParams

ButtonType = car.CarState.ButtonEvent.Type

GAS_RESUME_SPEED = 2.
cachedParams = CachedParams()
opParams = opParams()

class CarInterface(CarInterfaceBase):
  @staticmethod
  def compute_gb(accel, speed):
    return float(accel) / 3.0

  @staticmethod
  def get_params(candidate, fingerprint=gen_empty_fingerprint(), car_fw=None):
    speed_adjust_ratio = cachedParams.get_float('jvePilot.settings.speedAdjustRatio', 5000)
    min_steer_check = opParams.get('steer.checkMinimum')
    inverse_speed_adjust_ratio = 2 - speed_adjust_ratio

    ret = CarInterfaceBase.get_std_params(candidate, fingerprint)
    ret.carName = "chrysler"
    ret.safetyModel = car.CarParams.SafetyModel.chrysler

    # Chrysler port is a community feature, since we don't own one to test
    ret.communityFeature = True

    # TrafficFlow can steer to 0
    ret.minSteerSpeed = 0.

    # Speed conversion:              20, 45 mph
    ret.wheelbase = 3.089  # in meters for Pacifica Hybrid 2017
    ret.steerRatio = 16.2  # Pacifica Hybrid 2017
    ret.mass = 1964. + STD_CARGO_KG  # kg curb weight Pacifica Hybrid 2017

    ## NEW TUNE (wip) ##

    #ret.lateralTuning.pid.kpBP = [0., 10., 30.]
    #ret.lateralTuning.pid.kpV = [0.03, 0.05, 0.07]

    #ret.lateralTuning.pid.kiBP = [0., 30.]
    #ret.lateralTuning.pid.kiV = [0.02, 0.06]

    #ret.lateralTuning.pid.kdBP = [0.]
    #ret.lateralTuning.pid.kdV = [10.]

    #ret.lateralTuning.pid.kfBP = [0., 30.]
    #ret.lateralTuning.pid.kfV = [0.00002, 0.000035]   # full torque for 10 deg at 80mph means 0.00007818594
    #ret.lateralTuning.pid.kf = 0.000035 # [0.00002, 0.000035] # full torque for 10 deg at 80mph means 0.00007818594

    ## WORKING TUNE ###

    ret.lateralTuning.pid.kpBP, ret.lateralTuning.pid.kiBP = [[0., 10., 30.], [0., 30.]]
    ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.03, 0.05, 0.06], [0.02, 0.03]]
    ret.lateralTuning.pid.kf = 0.00002   # full torque for 10 deg at 80mph means 0.00007818594

    #ret.steerActuatorDelay = 0.1
    #ret.steerRateCost = 0.4
    #ret.steerLimitTimer = 0.7


    ### 17 GAS CHRYSLER PACIFICA SETTINGS ###

    #ret.steerActuatorDelay = 0.01
    #ret.steerRateCost = 0.7
    #ret.steerLimitTimer = 0.7

    ### OLD ###

    ret.steerActuatorDelay = 0.1
    ret.steerRateCost = 0.4
    ret.steerLimitTimer = 0.7

    ############

    if candidate in (CAR.JEEP_CHEROKEE, CAR.JEEP_CHEROKEE_2019):
      ret.wheelbase = 2.91  # in meters
      ret.steerRatio = 12.7
      ret.steerActuatorDelay = 0.2  # in seconds
      ret.enableBsm = True

    ret.centerToFront = ret.wheelbase * 0.44

    if min_steer_check:
      ret.minSteerSpeed = 3.8 * inverse_speed_adjust_ratio  # m/s
      if candidate in (CAR.PACIFICA_2019_HYBRID, CAR.PACIFICA_2020, CAR.JEEP_CHEROKEE_2019):
        # TODO allow 2019 cars to steer down to 13 m/s if already engaged.
        ret.minSteerSpeed = 17.5 * inverse_speed_adjust_ratio  # m/s 17 on the way up, 13 on the way down once engaged.

    # starting with reasonable value for civic and scaling by mass and wheelbase
    ret.rotationalInertia = scale_rot_inertia(ret.mass, ret.wheelbase)

    # TODO: start from empirically derived lateral slip stiffness for the civic and scale by
    # mass and CG position, so all cars will have approximately similar dyn behaviors
    ret.tireStiffnessFront, ret.tireStiffnessRear = scale_tire_stiffness(ret.mass, ret.wheelbase, ret.centerToFront)

    ret.enableCamera = True
    ret.openpilotLongitudinalControl = ret.enableCamera  # kind of...

    ret.enableBsm |= 720 in fingerprint[0]

    return ret

  # returns a car.CarState
  def update(self, c, can_strings):
    # ******************* do can recv *******************
    self.cp.update_strings(can_strings)
    self.cp_cam.update_strings(can_strings)

    ret = self.CS.update(self.cp, self.cp_cam)

    ret.canValid = self.cp.can_valid and self.cp_cam.can_valid

    # speeds
    ret.steeringRateLimited = self.CC.steer_rate_limited if self.CC is not None else False

    # events
    events = self.create_common_events(ret, extra_gears=[car.CarState.GearShifter.low],
                                       gas_resume_speed=GAS_RESUME_SPEED, pcm_enable=False)

    if ret.brakePressed and ret.vEgo < GAS_RESUME_SPEED:
      events.add(car.CarEvent.EventName.accBrakeHold)
    elif ret.vEgo < self.CP.minSteerSpeed:
      events.add(car.CarEvent.EventName.belowSteerSpeed)

    if self.CS.button_pressed(ButtonType.cancel):
      events.add(car.CarEvent.EventName.buttonCancel)  # cancel button pressed
    elif ret.cruiseState.enabled and not self.CS.out.cruiseState.enabled:
      events.add(car.CarEvent.EventName.pcmEnable)  # cruse is enabled
    elif (not ret.cruiseState.enabled) and (ret.vEgo > GAS_RESUME_SPEED or (self.CS.out.cruiseState.enabled and (not ret.standstill))):
      events.add(car.CarEvent.EventName.pcmDisable)  # give up, too fast to resume

    ret.events = events.to_msg()

    # copy back carState packet to CS
    self.CS.out = ret.as_reader()

    return self.CS.out

  # pass in a car.CarControl
  # to be called @ 100hz
  def apply(self, c):

    if (self.CS.frame == -1):
      return []  # if we haven't seen a frame 220, then do not update.

    can_sends = self.CC.update(c.enabled, self.CS, c.actuators, c.cruiseControl.cancel, c.hudControl.visualAlert,
                               GAS_RESUME_SPEED, c.jvePilotState)

    return can_sends
