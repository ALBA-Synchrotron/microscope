import clr
import time
import logging

clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.Benchtop.StepperMotorCLI.dll")
from Thorlabs.MotionControl.DeviceManagerCLI import *
from Thorlabs.MotionControl.GenericMotorCLI import *
from Thorlabs.MotionControl.Benchtop.StepperMotorCLI import *
from System import Decimal  # necessary for real world units

class ThorlabsControllerBSC201:

    def __init__(self, serialNo):
        DeviceManagerCLI.BuildDeviceList()
        self.serialNo = serialNo

        self.device = BenchtopStepperMotor.CreateBenchStepperMotor(self.serialNo)
        self.device.Connect(self.SerialNo)
        time.sleep(0.25)

        self.channel = self.device.GetChannel(1)

        # Ensure that the device settings have been initialized
        if not self.channel.IsSettingsInitialized():
            self.channel.WaitForSettingsInitialized(10000)  # 10 second timeout
            assert self.channel.IsSettingsInitialized() is True

        # Start polling and enable
        self.channel.StartPolling(250)  #250ms polling rate
        time.sleep(25)
        self.channel.EnableDevice()
        time.sleep(0.25)  # Wait for device to enable

        logging.info("Started homing ThorlabsBSC201: 60 sec timeout...")
        self.channel.Home(60000)
        logging.info("Finished homing procedure")

    def enable():
        pass

    def disable():
        pass

    def move(self, pos):
        self.channel.Move(pos)

    def moveRelative(self, units):
        self.channel.SetMoveRelativeDistance(units)
        self.channel.MoveRelative(10000)

        
