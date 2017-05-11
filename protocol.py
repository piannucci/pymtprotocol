import struct
import enum
import objc
import CoreBluetooth
from collections import namedtuple

"""
Define structured datatypes for some MT protocol messages.
"""


class GLMSettings(namedtuple('GLMSettings', 'spiritLevelEnabled, '
                             'dispRotationEnabled, speakerEnabled, '
                             'laserPointerEnabled, backlightMode, '
                             'angleUnit, measurementUnit')):
    @staticmethod
    def fromBytes(b):
        return GLMSettings(*struct.unpack('????BBBxxxx', b))

    def toBytes(self):
        return struct.pack('????BBBxxxx', *tuple(self))


class GLMDeviceInfo(namedtuple('GLMDeviceInfo', 'serialNumber, swRevision, '
                               'swVersionMain, swVersionSub, swVersionBug, '
                               'hwPCBVersion, hwPCBVariant, hwPCBBug, '
                               'unknown')):
    @staticmethod
    def fromBytes(b):
        return GLMDeviceInfo(*struct.unpack('xxxxihBBBBBB12sx', b))


class GLMSyncContainer(namedtuple('GLMSyncContainer', 'measurementType, '
                                  'calcIndicator, distReference, '
                                  'angleReference, distanceUnit, '
                                  'stateOfCharge, temperature, distance, '
                                  'result, angle, timestamp, laserOn, '
                                  'usabilityErrors, measurementListIndex, '
                                  'compassHeading, ndofSensorStatus')):
    @staticmethod
    def fromBytes(b):
        return GLMSyncContainer(
            measurementType=b[0] & 0x1f,
            calcIndicator=b[0] >> 5,
            distReference=b[1] & 7,
            angleReference=(b[1] >> 3) & 7,
            distanceUnit=(b[1] >> 6) & 1,
            stateOfCharge=b[2],
            temperature=b[3],
            distance=struct.unpack_from('fff', b, 4),
            result=struct.unpack_from('f', b, 16)[0],
            angle=struct.unpack_from('f', b, 20)[0],
            timestamp=struct.unpack_from('i', b, 24)[0],
            laserOn=b[28] & 1,
            usabilityErrors=b[28] >> 1,
            measurementListIndex=b[29],
            compassHeading=struct.unpack_from('h', b, 30)[0],
            ndofSensorStatus=b[32],
        )


class GLMPayloadSize(namedtuple('GLMPayloadSize', 'RXPayloadSize, '
                                'TXPayloadSize')):
    @staticmethod
    def fromBytes(b):
        return GLMPayloadSize(*struct.unpack('xxxxHH', b))


class GLMProtocolVersion(namedtuple('GLMProtocolVersion', 'Main, Sub, Bug, '
                                    'ProjMain, ProjSub, ProjBug')):
    @staticmethod
    def fromBytes(b):
        return GLMProtocolVersion(*b)


class GLMRealTimeClock(namedtuple('GLMRealTimeClock', 'clockSeconds')):
    @staticmethod
    def fromBytes(b):
        return GLMRealTimeClock(*struct.unpack('I', b))


class GLMUploadResult(namedtuple('GLMUploadResult', 'uploadErrorCode, '
                                 'blockNumber')):
    @staticmethod
    def fromBytes(b):
        return GLMUploadResult(uploadErrorCode=b[0] & 0xf,
                               blockNumber=b[0] >> 4)


GLM_SERVICE_UUID = CoreBluetooth.CBUUID.alloc() \
                    .initWithString_("00005301-0000-0041-5253-534F46540000")
TX_CHARACTERISTIC_UUID = CoreBluetooth.CBUUID.alloc() \
                    .initWithString_("00004301-0000-0041-5253-534F46540000")
RX_CHARACTERISTIC_UUID = CoreBluetooth.CBUUID.alloc() \
                    .initWithString_("00004302-0000-0041-5253-534F46540000")


class DistReference(enum.IntEnum):
    Front, Center, Back, Tripod = range(4)


class DistanceUnit(enum.IntEnum):
    Metric, Imperial = range(2)


class CRCError(Exception):
    pass


class StatusError(Exception):
    def __init__(self, number):
        string = [
            'Success', 'CommunicationTimeout', 'ModeInvalid', 'ChecksumError',
            'UnknownCommand', 'InvalidAccessLevel', 'InvalidDatabytes',
            'Reserved'
        ][number & 7]
        if number & 8:
            string += ' | HardwareError'
        if number & 16:
            string += ' | DeviceNotReady'
        if number & 32:
            string += ' | HandRaised'
        super().__init__(string)


def crc8(data, iv=0xaa, poly=0xa6):
    value = iv
    for b in data:
        for i in range(8):
            x, value = (value >> 7) ^ (b >> (7-i)) & 1, (value << 1) & 0xff
            if x:
                value ^= poly
    return value
