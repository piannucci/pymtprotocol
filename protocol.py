import struct, enum
import objc, CoreBluetooth

"""
Define structured datatypes for some MT protocol messages.
"""
GLMSettings = objc.createStructType('GLMSettings', b'{GLMSettings=BBBBCCC[4c]}', [
    'spiritLevelEnabled', 'dispRotationEnabled', 'speakerEnabled', 'laserPointerEnabled',
    'backlightMode', 'angleUnit', 'measurementUnit', 'reserved'
])
GLMSettings_fromBytes = lambda b: GLMSettings(*struct.unpack('????BBBxxxx', b))
GLMSettings_toBytes = lambda s: struct.pack('????BBBxxxx', *tuple(s)[:-1])

GLMDeviceInfo = objc.createStructType('GLMDeviceInfo', b'{GLMDeviceInfo=isCCCCCC[12c]}', [
    'serialNumber', 'swRevision', 'swVersionMain', 'swVersionSub', 'swVersionBug',
    'hwPCBVersion', 'hwPCBVariant', 'hwPCBBug', 'unknown'
])
GLMDeviceInfo_fromBytes = lambda b: GLMDeviceInfo(*struct.unpack('xxxxihBBBBBB12sx', b))

GLMSyncContainer = objc.createStructType('GLMSyncContainer', b'{GLMSyncContainer=BBBBBBB[3f]ffiBBBsB}', [
    'measurementType', 'calcIndicator', 'distReference', 'angleReference',
    'distanceUnit', 'stateOfCharge', 'temperature', 'distance', 'result',
    'angle', 'timestamp', 'laserOn', 'usabilityErrors', 'measurementListIndex',
    'compassHeading', 'ndofSensorStatus'
])
def GLMSyncContainer_fromBytes(b):
    c = GLMSyncContainer()
    c.measurementType       = b[0] & 0x1f
    c.calcIndicator         = b[0] >> 5
    c.distReference         = b[1] & 7
    c.angleReference        = (b[1] >> 3) & 7
    c.distanceUnit          = (b[1] >> 6) & 1
    c.stateOfCharge         = b[2]
    c.temperature           = b[3]
    *c.distance, c.result, c.angle, c.timestamp = struct.unpack('fffffi', b[4:28])
    c.laserOn               = b[28] & 1
    c.usabilityErrors       = b[28] >> 1
    c.measurementListIndex  = b[29]
    c.compassHeading,       = struct.unpack('h', b[30:32])
    c.ndofSensorStatus      = b[32]
    return c

def GLMPayloadSize_fromBytes(b):
    return dict(zip(['RXPayloadSize', 'TXPayloadSize'], struct.unpack('xxxxHH', b)))

def GLMProtocolVersion_fromBytes(b):
    return dict(zip(['Main', 'Sub', 'Bug', 'ProjMain', 'ProjSub', 'ProjBug'], b))

def GLMRealTimeClock_fromBytes(b):
    clockSeconds, = struct.unpack('I', b)
    return clockSeconds

def GLMUploadResult_fromBytes(b):
    return dict(uploadErrorCode=b[0] & 0xf, blockNumber=b[0] >> 4)

GLM_SERVICE_UUID = CoreBluetooth.CBUUID.alloc().initWithString_("00005301-0000-0041-5253-534F46540000")
TX_CHARACTERISTIC_UUID = CoreBluetooth.CBUUID.alloc().initWithString_("00004301-0000-0041-5253-534F46540000")
RX_CHARACTERISTIC_UUID = CoreBluetooth.CBUUID.alloc().initWithString_("00004302-0000-0041-5253-534F46540000")

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
            'UnknownCommand', 'InvalidAccessLevel', 'InvalidDatabytes', 'Reserved'
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
            if x: value ^= poly
    return value
