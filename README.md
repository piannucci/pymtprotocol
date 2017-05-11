# pymtprotocol
The Bosch GLM 100 C Professional is a battery-powered laser measurer with a number of handy onboard sensors.  In addition to the expected laser range finder, it includes an inclinometer, digital compass, thermometer, and battery voltage indicator.  The device is Bluetooth Low Energy (BLE) enabled, and applications are available for Windows, iOS, and Android for syncing data from the device, configuring its mode and settings remotely, and contact-free measurement triggering.

This repository contains a complete re-implementation of the discovery, connection, acknowledgement, fragmentation, and reassembly protocol stack found in the official Bosch apps.  It is currently OS X only, and probably requires Python 3.4.
