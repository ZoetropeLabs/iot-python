# *****************************************************************************
# Copyright (c) 2014, 2018 IBM Corporation and other Contributors.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Eclipse Public License v1.0
# which accompanies this distribution, and is available at
# http://www.eclipse.org/legal/epl-v10.html
#
# Contributors:
#   David Parker
#   Paul Slater
#   Ben Bakowski
#   Amit M Mangalvedkar
#   Lokesh Haralakatta
# *****************************************************************************

import sys
import os
import time
import json
import socket
import ssl
import logging
from logging.handlers import RotatingFileHandler
import paho.mqtt.client as paho
import threading
import iso8601
import pytz
from datetime import datetime
from encodings.base64_codec import base64_encode

__version__ = "0.3.2"

def _getBrokerAddress(domain = None, orgId = None, completeBrokerUrl = None):
    # Compute broker adress
    if not completeBrokerUrl and (not domain or not orgId):
        raise ConfigurationException(
            "No full broker URL given, so both domain and organisation "
            "must be specified"
        )
    return (
        completeBrokerUrl if completeBrokerUrl
        else orgId + '.messaging.' + domain
    )

class Message:
    def __init__(self, data, timestamp=None):
        self.data = data
        self.timestamp = timestamp

class AbstractClient:
    def __init__(self, domain, organization, clientId, username, password, port=None,
                 logHandlers=None, cleanSession="true",
                 completeBrokerUrl=None, disableTLS=False, useWebsockets=False,
                 keepAlive=60, tlsVersion="PROTOCOL_TLSv1_2"):
        self.organization = organization
        self.username = username
        self.password = password
        self.address = _getBrokerAddress(domain, organization, completeBrokerUrl)
        self.keepAlive = keepAlive

        self.connectEvent = threading.Event()

        self._recvLock = threading.Lock()
        self._messagesLock = threading.Lock()

        self.messages = 0
        self.recv = 0

        self.clientId = clientId

        # Configure logging
        self.logger = logging.getLogger(__name__)

        if useWebsockets:
            transport = "websockets"
        else:
            transport = "tcp"

        if port is None:
            if transport == "tcp":
                port = 8883
            else:
                port = 9001

        self.port = port

        self.logger.debug("Using transport %s", transport)
        self.logger.debug("Using port %d", port)

        clean_session = (False if cleanSession == "false" else True)

        self._paho_userdata = {}
        self.client = paho.Client(
            self.clientId,
            clean_session=clean_session,
            transport=transport,
            userdata=self._paho_userdata,
        )

        self.client.enable_logger()

        if disableTLS:
            self.logger.warning("TLS force disabled")
        else:
            self.logger.debug("Trying to enable '%s'", tlsVersion)
            try:
                self.tlsVersion = getattr(ssl, tlsVersion)
            except AttributeError:
                self.logger.warning("Unable to enable TLS", exc_info=True)
            else:
                # Path to certificate
                if "internetofthings.ibmcloud.com" in self.address:
                    caFile = os.path.dirname(os.path.abspath(__file__)) + "/messaging.pem"
                else:
                    caFile = None
                self.logger.debug("Using TLS - caFile = %s", caFile)
                self.client.tls_set(ca_certs=caFile, certfile=None, keyfile=None, cert_reqs=ssl.CERT_REQUIRED, tls_version=self.tlsVersion)

        # Configure authentication
        if self.username is not None:
            self.client.username_pw_set(self.username, self.password)

        # Attach MQTT callbacks
        self.client.on_log = self.on_log
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_publish = self.on_publish

        # Initialize default message encoders and decoders.
        self._messageEncoderModules = {}

        self.start = time.time()

        # initialize callbacks
        self._onPublishCallbacks = {}


    def getMessageEncoderModule(self, messageFormat):
        return self._messageEncoderModules[messageFormat]

    def setMessageEncoderModule(self, messageFormat, module):
        self._messageEncoderModules[messageFormat] = module

    def logAndRaiseException(self, e):
        self.logger.critical(str(e))
        raise e

    def connect(self):
        self.logger.debug("Connecting... (address = %s, port = %s, clientId = %s, username = %s, password = %s)" % (self.address, self.port, self.clientId, self.username, self.password))
        try:
            self.connectEvent.clear()
            self.client.connect(self.address, port=self.port, keepalive=self.keepAlive)
            self.client.loop_start()
            if not self.connectEvent.wait(timeout=30):
                self.client.loop_stop()
                self.logAndRaiseException(ConnectionException("Operation timed out connecting to IBM Watson IoT Platform: %s" % (self.address)))

        except socket.error as serr:
            self.client.loop_stop()
            self.logAndRaiseException(ConnectionException("Failed to connect to IBM Watson IoT Platform: %s - %s" % (self.address, str(serr))))

    def disconnect(self):
        #self.logger.info("Closing connection to the IBM Watson IoT Platform")
        self.client.disconnect()
        # If we don't call loop_stop() it appears we end up with a zombie thread which continues to process
        # network traffic, preventing any subsequent attempt to reconnect using connect()
        self.client.loop_stop()
        #self.stats()
        self.logger.info("Closed connection to the IBM Watson IoT Platform")

    def stats(self):
        elapsed = ((time.time()) - self.start)

        msgPerSecond = 0 if self.messages == 0 else elapsed/self.messages
        recvPerSecond = 0 if self.recv == 0 else elapsed/self.recv
        self.logger.debug("Messages published : %s, life: %.0fs, rate: 1/%.2fs" % (self.messages, elapsed, msgPerSecond))
        self.logger.debug("Messages received  : %s, life: %.0fs, rate: 1/%.2fs" % (self.recv, elapsed, recvPerSecond))


    def on_log(self, mqttc, obj, level, string):
        self.logger.debug("%s" % (string))



    '''
    This is called when the client disconnects from the broker. The rc parameter indicates the status of the disconnection.
    When 0 the disconnection was the result of disconnect() being called, when 1 the disconnection was unexpected.
    '''
    def on_disconnect(self, mosq, obj, rc):
        if rc == 1:
            self.logger.error("Unexpected disconnect from the IBM Watson IoT Platform")
        else:
            self.logger.info("Disconnected from the IBM Watson IoT Platform")
        self.stats()

    '''
    This is called when a message from the client has been successfully sent to the broker.
    The mid parameter gives the message id of the successfully published message.
    '''
    def on_publish(self, mosq, obj, mid):
        with self._messagesLock:
            self.messages = self.messages + 1
            if mid in self._onPublishCallbacks:
                midOnPublish = self._onPublishCallbacks.get(mid)
                del self._onPublishCallbacks[mid]
                midOnPublish()
            else:
                # record the fact that paho callback has already come through so it can be called inline
                # with the publish.
                self._onPublishCallbacks[mid] = None

    '''
    Setter and Getter methods to set and get user defined keepAlive Interval  value to
    override the MQTT default value of 60
    '''
    def setKeepAliveInterval(self, newKeepAliveInterval):
        self.keepAlive = newKeepAliveInterval

    def getKeepAliveInterval(self):
        return(self.keepAlive)



'''
Generic Connection exception "Something went wrong"
'''
class ConnectionException(Exception):
    def __init__(self, reason):
        self.reason = reason

    def __str__(self):
        return self.reason

'''
Specific Connection exception where the configuration is invalid
'''
class ConfigurationException(ConnectionException):
    def __init__(self, reason):
        self.reason = reason

    def __str__(self):
        return self.reason


'''
Specific Connection exception where the authentication method specified is not supported
'''
class UnsupportedAuthenticationMethod(ConnectionException):
    def __init__(self, method):
        self.method = method

    def __str__(self):
        return "Unsupported authentication method: %s" % self.method


'''
Specific exception where and Event object can not be constructed
'''
class InvalidEventException(Exception):
    def __init__(self, reason):
        self.reason = reason

    def __str__(self):
        return "Invalid Event: %s" % self.reason


'''
Specific exception where and Event object can not be constructed
'''
class MissingMessageDecoderException(Exception):
    def __init__(self, format):
        self.format = format

    def __str__(self):
        return "No message decoder defined for message format: %s" % self.format


class MissingMessageEncoderException(Exception):
    def __init__(self, format):
        self.format = format

    def __str__(self):
        return "No message encoder defined for message format: %s" % self.format


'''
This exception has been added in V2 and provides the following
1) The exact HTTP Status Code
2) The error thrown
3) The JSON message returned
'''
class APIException(Exception):
    def __init__(self, httpCode, message, response):
        self.httpCode = httpCode
        self.message = message
        self.response = response

    def __str__(self):
        return "[%s] %s" % (self.httpCode, self.message)

class HttpAbstractClient:
    def __init__(self, clientId, logHandlers=None):
        # Configure logging
        self.logger = logging.getLogger(self.__module__+"."+self.__class__.__name__)
        self.logger.setLevel(logging.INFO)

        # Remove any existing log handlers we may have picked up from getLogger()
        self.logger.handlers = []

        if logHandlers:
            if isinstance(logHandlers, list):
                # Add all supplied log handlers
                for handler in logHandlers:
                    self.logger.addHandler(handler)
            else:
                # Add the supplied log handler
                self.logger.addHandler(logHandlers)
        else:
            # Generate a default rotating file log handler and stream handler
            logFileName = '%s.log' % (clientId.replace(":", "_"))
            fhFormatter = logging.Formatter('%(asctime)-25s %(name)-25s ' + ' %(levelname)-7s %(message)s')
            rfh = RotatingFileHandler(logFileName, mode='a', maxBytes=1024000 , backupCount=0, encoding=None, delay=True)
            rfh.setFormatter(fhFormatter)

            ch = logging.StreamHandler()
            ch.setFormatter(fhFormatter)
            ch.setLevel(logging.DEBUG)

            self.logger.addHandler(rfh)
            self.logger.addHandler(ch)

           # Initialize default message encoders and decoders.
        self._messageEncoderModules = {}

    def connect(self):
        # No-op with HTTP client (but makes it easy to switch between using http & mqtt clients in your code)
        pass

    def disconnect(self):
        # No-op with HTTP client (but makes it easy to switch between using http & mqtt clients in your code)
        pass

    def getMessageEncoderModule(self, messageFormat):
        return self._messageEncoderModules[messageFormat]

    def setMessageEncoderModule(self, messageFormat, module):
        self._messageEncoderModules[messageFormat] = module

    def logAndRaiseException(self, e):
        self.logger.critical(str(e))
        raise e

    def getContentType(self,dataFormat):
        '''
           Method to detect content type using given data format
        '''
        # Default content type is json
        contentType = "application/json"
        if dataFormat == "text":
            contentType = "text/plain; charset=utf-8"
        elif dataFormat == "xml":
            contentType = "application/xml"
        elif dataFormat == "bin":
            contentType = "application/octet-stream"
        else:
            contentType = "application/json"
        # Return derived content type
        return contentType
