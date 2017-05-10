import sys, threading, asyncio, queue, struct, binascii, contextlib
import objc, Foundation, CoreBluetooth
import osx, async
from protocol import *

# XXX track RSSI, battery, temperature and warn

LOG_LEVEL = 0
def log(level, *args):
    if level <= LOG_LEVEL:
        print(*args, flush=True)

class PeripheralController(Foundation.NSObject, protocols=[objc.protocolNamed('CBPeripheralDelegate')]):
    def initWithPeripheral_queue_(self, peripheral, dispatchQueue):
        self = objc.super(PeripheralController, self).init()
        if self is not None:
            self.queue = dispatchQueue
            self.peripheral = peripheral
            peripheral.setDelegate_(self)
            log(1, 'Scanning for services on %s' % self.peripheral)
            peripheral.discoverServices_([GLM_SERVICE_UUID])

            self.write_lock = threading.Lock()
            self.deferred_writes = queue.Queue()
            self.submitted_writes = queue.Queue()

            self.read_stream = async.FutureStream()

            self.assembly_buffer = b''
            self.assembly_seqno = -1
            self.tx_seqno = 1

            self.ready_gates = {'txchar':False, 'rxchar':False, 'notify':False}
            self.ready = async.Fuse()

            self.disconnected = async.Fuse()
        return self

    def peripheral_didDiscoverServices_(self, peripheral, services):
        for service in peripheral.services():
            if service.UUID() == GLM_SERVICE_UUID:
                log(1, 'Scanning GLM service for characteristics')
                peripheral.discoverCharacteristics_forService_([TX_CHARACTERISTIC_UUID, RX_CHARACTERISTIC_UUID], service)

    @objc.python_method
    def updateReadiness(self, **kwargs):
        self.ready_gates.update(kwargs)
        if not self.ready and all(self.ready_gates.values()):
            self.ready.trigger()

    def peripheral_didDiscoverCharacteristicsForService_error_(self, peripheral, service, error):
        if error:
            self.ready.trigger(exception=Exception(error))
        else:
            for characteristic in service.characteristics():
                if characteristic.UUID() == TX_CHARACTERISTIC_UUID:
                    self.tx_characteristic = characteristic
                    self.updateReadiness(txchar=True)
                elif characteristic.UUID() == RX_CHARACTERISTIC_UUID:
                    self.rx_characteristic = characteristic
                    self.updateReadiness(rxchar=True)
                    peripheral.setNotifyValue_forCharacteristic_(1, characteristic)

    def peripheral_didUpdateNotificationStateForCharacteristic_error_(self, peripheral, characteristic, error):
        if not error:
            self.updateReadiness(notify=True)

    def peripheral_didUpdateValueForCharacteristic_error_(self, peripheral, characteristic, error):
        if error:
            log(2, 'didUpdate: %s' % error)
            self.read_stream.post(exception=Exception(error))
        else:
            value = characteristic.value()
            log(2, 'didUpdate: %s' % binascii.hexlify(value).decode())
            if value[0] == 0xff:
                self.sendChunk()
            else:
                seqno = value[0]
                ack = bytes([0xff, seqno, 0x00])
                log(2, 'willWrite: %s' % binascii.hexlify(ack).decode())
                self.submitted_writes.put(None)
                self.peripheral.writeValue_forCharacteristic_type_(ack, self.tx_characteristic, CoreBluetooth.CBCharacteristicWriteWithResponse)
                if seqno != self.assembly_seqno - 1:
                    self.assembly_buffer = b''
                self.assembly_seqno = seqno
                self.assembly_buffer += value[1:]
                if seqno & 0xf == 0:
                    frame = self.assembly_buffer
                    if crc8(frame) != 0:
                        self.read_stream.post(exception=CRCError())
                    else:
                        status = frame[0] & 0x3f
                        frameType = (frame[0] & 0xc0) >> 6
                        headerLength = 0 if (frameType == 0) else 1
                        payload = frame[2+headerLength:2+headerLength+frame[1+headerLength]]
                        if frameType == 0: # response
                            self.read_stream.post(result=(status, payload))
                        elif frameType == 3: # request
                            command = frame[1]
                            self.handleRequest(status, command, payload)

    @objc.python_method
    def handleRequest(self, status, command, payload):
        if command == 0x50:
            payload = GLMSyncContainer_fromBytes(payload)
            log(0, 'sync: %s' % payload)

    def peripheral_didWriteValueForCharacteristic_error_(self, peripheral, characteristic, error):
        log(2, 'didWrite')
        try:
            async.complete(self.submitted_writes.get(False), exception=Exception(error) if error else None)
        except queue.Empty:
            log(2, 'unexpected write callback')
            pass

    @objc.python_method
    def sendChunk(self):
        with self.write_lock:
            while True:
                try:
                    future, item = self.deferred_writes.get(False)
                except queue.Empty:
                    return
                if item is not None:
                    log(2, 'willWrite: %s' % binascii.hexlify(item).decode())
                    self.submitted_writes.put(future)
                    self.peripheral.writeValue_forCharacteristic_type_(item, self.tx_characteristic, CoreBluetooth.CBCharacteristicWriteWithResponse)
                else:
                    async.complete(future)

    @objc.python_method
    def didDisconnect(self, error):
        self.read_stream.set_exception(Exception(error))
        self.disconnected.trigger(exception=Exception(error))

    @objc.python_method
    @asyncio.coroutine
    def waitUntilReady(self):
        with self.ready() as f:
            yield from f

    @objc.python_method
    @asyncio.coroutine
    def sendRequest(self, command, payload):
        yield from self.waitUntilReady()
        with self.disconnected() as f:
            if not f.done():
                frame = b'\xC0' + bytes([command, len(payload)]) + payload
                frame += bytes([crc8(frame)])
                count = (len(frame) + 18) // 19
                for i in range(count):
                    fragment = bytes([(self.tx_seqno << 4) | (count-1-i)]) + frame[19*i:19*(i+1)]
                    self.deferred_writes.put((f if i==count-1 else None, fragment))
                self.tx_seqno = (self.tx_seqno + 1) % 15
                osx.dispatch_async(self.queue, self.sendChunk)
            yield from f
        status, payload = (yield from self.read())
        if status != 0:
            raise StatusError(status)
        return payload

    @objc.python_method
    @asyncio.coroutine
    def flush(self):
        yield from self.waitUntilReady()
        with self.disconnected() as f:
            if not f.done():
                self.deferred_writes.put((f, None))
            yield from f

    @objc.python_method
    @asyncio.coroutine
    def read(self):
        yield from self.waitUntilReady()
        return (yield from self.read_stream.claim())

    @objc.python_method
    @asyncio.coroutine
    def readSettings(self):
        return GLMSettings_fromBytes((yield from self.sendRequest(0x53, b'')))

    @objc.python_method
    @asyncio.coroutine
    def writeSettings(self, settings=None, **kwargs):
        if settings is None:
            settings = yield from self.readSettings()
        settings = settings._replace(**kwargs)
        yield from self.sendRequest(0x54, GLMSettings_toBytes(settings))

    @objc.python_method
    @asyncio.coroutine
    def serialNumber(self):
        return (yield from self.deviceInfo()).serialNumber

    @objc.python_method
    @asyncio.coroutine
    def deviceInfo(self):
        return GLMDeviceInfo_fromBytes((yield from self.sendRequest(0x06, b'')))

    @objc.python_method
    @asyncio.coroutine
    def getMeasurements(self, first, last):
        results = []
        while first <= last:
            payload = yield from self.sendRequest(0x51, bytes([first, last]))
            count = (len(payload)-2) // 33
            if count == 0 or payload[0] != first:
                break
            results.extend([payload[i:i+33] for i in range(2, len(payload), 33)])
            first = payload[1]+1
        return [GLMSyncContainer_fromBytes(b) for b in results]

    @objc.python_method
    @asyncio.coroutine
    def clearMeasurements(self, first, last):
        return (yield from self.sendRequest(0x52, bytes([first, last])))

    @objc.python_method
    @asyncio.coroutine
    def control(self, **kwargs):
        switchMode = kwargs.get('switchMode', 0)
        syncControl = kwargs.get('syncControl', 0)
        signalOperation = kwargs.get('signalOperation', 0)
        measurementType = kwargs.get('measurementType', 0)
        angleReference = kwargs.get('angleReference', 0)
        distReference = kwargs.get('distReference', 0)
        payload = bytes([
            (switchMode << 7) | ((syncControl & 1) << 6) | ((signalOperation & 1) << 5) | (measurementType & 0x1f),
            ((angleReference & 7) << 3) | (distReference & 7),
        ])
        return GLMSyncContainer_fromBytes((yield from self.sendRequest(0x50, payload)))

    @objc.python_method
    @asyncio.coroutine
    def payloadSize(self):
        return GLMPayloadSize_fromBytes((yield from self.sendRequest(0x00, b'')))

    @objc.python_method
    @asyncio.coroutine
    def MTProtocolVersion(self):
        return GLMProtocolVersion_fromBytes((yield from self.sendRequest(0x04, b'')))

    @objc.python_method
    @asyncio.coroutine
    def deviceRealTimeClock(self):
        return GLMRealTimeClock_fromBytes((yield from self.sendRequest(0x0f, b'')))

    @objc.python_method
    @asyncio.coroutine
    def deviceInfoString(self):
        return (yield from self.sendRequest(0x3a, b''))

    @objc.python_method
    @asyncio.coroutine
    def uploadBlock(self, blockNumber, blockType, chunkData):
        payload = bytes([(blockNumber << 4) | blockType, len(chunkData)]) + chunkData
        return GLMUploadResult_fromBytes((yield from self.sendRequest(0x3b, payload)))

    # setDeviceMaster(self): yield from self.control(syncControl=1, signalOperation=1)

    @objc.python_method
    @asyncio.coroutine
    def setLaserPower(self, value):
        yield from self.writeSettings(laserPointerEnabled=value)

    @objc.python_method
    @asyncio.coroutine
    def turnOnAutoSync(self):
        yield from self.control(syncControl=1)

    @objc.python_method
    @asyncio.coroutine
    def measureDistance(self, distReference=DistReference.Tripod, metric=True):
        settings = yield from self.readSettings()
        if settings.measurementUnit != DistanceUnit.Metric:
            self.writeSettings(settings, measurementUnit=DistanceUnit.Metric)
        laserOn = settings.laserPointerEnabled
        if not laserOn:
            yield from glm.control(switchMode=0, measurementType=1, distReference=distReference)
        return (yield from glm.control(switchMode=0, measurementType=1, distReference=distReference)).result

class CentralController(Foundation.NSObject, protocols=[objc.protocolNamed('CBCentralManagerDelegate')]):
    def initWithQueue_knownDevices_(self, queue, known_devices):
        self = objc.super(CentralController, self).init()
        if self is not None:
            self.queue = queue
            self.wantedPeripherals = known_devices
            self.knownPeripherals = {}
            self.connectingPeripherals = {}
            self.connectedPeripherals = {}
            self.centralManager = CoreBluetooth.CBCentralManager.alloc().initWithDelegate_queue_(self, osx.dispatch_queue_from_id(queue))
            self.timer = osx.DispatchTimer(4, queue, self.timerFired)
            Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, Foundation.kCFRunLoopCommonModes)
            self.connect = async.KeyedEvent()
        return self

    @objc.python_method
    def timerFired(self, context):
        self.centralManagerDidUpdateState_(self.centralManager)

    def centralManagerDidUpdateState_(self, centralManager):
        state = centralManager.state()
        if state < CoreBluetooth.CBCentralManagerStatePoweredOff:
            self.knownPeripherals = {}
            self.connectingPeripherals = {}
            self.connectedPeripherals = {}
        if state == CoreBluetooth.CBCentralManagerStatePoweredOn:
            log(1, 'Bluetooth is on')

            for peripheral in self.retrieveWantedPeripherals():
                self.discovered(peripheral)

            wanted = set(self.wantedPeripherals)
            known = set(self.knownPeripherals.keys())
            for uuidString in wanted.intersection(known):
                self.discovered(self.knownPeripherals[uuidString])
            if wanted - known:
                log(1, 'Scanning for peripherals')
                centralManager.scanForPeripheralsWithServices_options_(None, {})
            else:
                log(1, 'Stopping scan')
                centralManager.stopScan()

        elif state == CoreBluetooth.CBCentralManagerStateUnsupported:
            log(0, 'Bluetooth Low Energy not supported on this hardware')
            sys.exit(-1)
        elif state == CoreBluetooth.CBCentralManagerStateUnauthorized:
            log(0, 'Permission denied to use Bluetooth Low Energy')
            sys.exit(-1)
        elif state == CoreBluetooth.CBCentralManagerStatePoweredOff:
            log(1, 'Turning Bluetooth on')
            osx.setBluetoothPowerState(1)

    @objc.python_method
    def discovered(self, peripheral):
        uuidString = peripheral.identifier().UUIDString()
        if uuidString in self.wantedPeripherals:
            if uuidString not in self.knownPeripherals:
                self.knownPeripherals[uuidString] = peripheral
            if uuidString not in self.connectingPeripherals and uuidString not in self.connectedPeripherals:
                log(0, 'Connecting to %s' % peripheral)
                self.connectingPeripherals[uuidString] = peripheral
                self.centralManager.connectPeripheral_options_(peripheral, {})

    def centralManager_didDiscoverPeripheral_advertisementData_RSSI_(self, centralManager, peripheral, advertisementData, rssi):
        self.discovered(peripheral)

    def centralManager_didDisconnectPeripheral_error_(self, centralManager, peripheral, error):
        log(0, 'Disconnected %s %s' % (peripheral, error))
        uuidString = peripheral.identifier().UUIDString()
        # what cleanup is needed?
        if uuidString in self.connectingPeripherals:
            del self.connectingPeripherals[uuidString]
        if uuidString in self.connectedPeripherals:
            self.connectedPeripherals[uuidString].didDisconnect(error)
            del self.connectedPeripherals[uuidString]
        self.connect.trigger(uuidString, exception=Exception(error))

    def centralManager_didFailToConnectPeripheral_error_(self, centralManager, peripheral, error):
        log(0, 'Failed to connect %s %s' % (peripheral, error))
        uuidString = peripheral.identifier().UUIDString()
        if uuidString in self.connectingPeripherals:
            del self.connectingPeripherals[uuidString]
        self.connect.trigger(uuidString, exception=Exception(error))

    def centralManager_didConnectPeripheral_(self, centralManager, peripheral):
        log(0, 'Connected %s' % peripheral)
        uuidString = peripheral.identifier().UUIDString()
        if not uuidString in self.connectedPeripherals:
            p = PeripheralController.alloc().initWithPeripheral_queue_(peripheral, self.queue)
            self.connectedPeripherals[uuidString] = p
        else:
            p = self.connectedPeripherals[uuidString]
        self.connect.trigger(uuidString, p)

    @objc.python_method
    def retrieveWantedPeripherals(self):
        uuids = [Foundation.NSUUID.alloc().initWithUUIDString_(s) for s in self.wantedPeripherals]
        return self.centralManager.retrievePeripheralsWithIdentifiers_(uuids)

    @objc.python_method
    @asyncio.coroutine
    def deviceFromUUIDString(self, uuidString):
        if uuidString not in self.wantedPeripherals:
            self.wantedPeripherals.append(uuidString)
            peripherals = self.retrieveWantedPeripherals()
        with self.connect(uuidString) as f:
            try:
                f.set_result(self.connectedPeripherals[uuidString])
            except (KeyError, asyncio.futures.InvalidStateError):
                pass
            return (yield from f)

@asyncio.coroutine
def runBluetoothCentralManager(ready, known_peripheral_uuids):
    async.set_default_loop(asyncio.get_event_loop())
    queue = osx.dispatch_get_global_queue(osx.QOS_CLASS_DEFAULT, 0)
    controller = CentralController.alloc().initWithQueue_knownDevices_(queue, known_peripheral_uuids)
    global glm
    while True:
        glm = yield from controller.deviceFromUUIDString(known_peripheral_uuids[0])
        async.complete(ready)
        try:
            with glm.disconnected() as f:
                yield from f
        except:
            pass

ready = asyncio.Future()
loop = asyncio.get_event_loop()
loop.create_task(runBluetoothCentralManager(ready, ["32F69959-1D4E-40F3-AFFE-D1AC44F80A9E"]))
loop.run_until_complete(ready)
print(loop.run_until_complete(glm.measureDistance()))
