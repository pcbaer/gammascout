#
#	GammaScoutUtil - Tool to communicate with Gamma Scout Geiger counters.
#	Copyright (C) 2011-2011 Johannes Bauer
#	
#	This file is part of GammaScoutUtil.
#
#	GammaScoutUtil is free software; you can redistribute it and/or modify
#	it under the terms of the GNU General Public License as published by
#	the Free Software Foundation; this program is ONLY licensed under
#	version 3 of the License, later versions are explicitly excluded.
#
#	GammaScoutUtil is distributed in the hope that it will be useful,
#	but WITHOUT ANY WARRANTY; without even the implied warranty of
#	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#	GNU General Public License for more details.
#
#	You should have received a copy of the GNU General Public License
#	along with GammaScoutUtil; if not, write to the Free Software
#	Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
#	Johannes Bauer <JohannesBauer@gmx.de>
#

import sys
import time
import datetime
import serial
from threading import Thread, Condition
from RE import RE

class GSConnection(Thread):
	_version_pc_regex = RE("Version ([0-9]\.[0-9]{2}) " + RE.GDECIMAL + " " + RE.GHEXADECIMAL + " ([0-9]{2})\.([0-9]{2})\.([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})")
	_config_firstline_regex = RE(RE.GHEXADECIMAL + " " + RE.GHEXADECIMAL + " " + RE.GHEXADECIMAL)

	def __init__(self, device, debugmode = False):
		Thread.__init__(self)
		self._debugmode = debugmode
		self._conn = serial.Serial(device, baudrate = 9600, bytesize = 7, parity = "E", stopbits = 1, timeout = 0.1)
		self._quit = False
		self._databuffer = [ ]
		self._linebuffer = ""
		self._bufferhasitems = Condition()
		self.start()

	def _debug(self, msg):
		if self._debugmode:
			print("%10.3f: %s" % (time.time(), msg), file = sys.stderr)
	
	def _nextmsg(self, timeout = 1.0):
		result = None
		timeout_end = time.time() + timeout
		self._bufferhasitems.acquire()
		while (len(self._databuffer) == 0) and (time.time() < timeout_end):
			self._bufferhasitems.wait(timeout_end - time.time())
		if len(self._databuffer) > 0:
			result = self._databuffer[0]
			self._databuffer = self._databuffer[1:]
		self._bufferhasitems.release()
		self._debug("# " + str(result))
		return result

	def _expectresponse(self, string, timeout = 1.0):
		assert(isinstance(string, str))
		datagram = self._nextmsg(timeout)
		if datagram != "":
			raise Exception("Waiting for first response datagram returned '%s' while expecting empty string." % (str(datagram)))
		
		datagram = self._nextmsg(timeout)
		if datagram != string:
			raise Exception("Waiting for second response datagram returned '%s' while expecting '%s'." % (str(datagram), string))

	def _rxdata(self, data):
		self._linebuffer += data.decode("latin1")
		datagrams = self._linebuffer.split("\r\n")
		self._debug("<- " + str(datagrams))
		self._linebuffer = datagrams[-1]

		if len(datagrams) > 1:
			self._bufferhasitems.acquire()
			for datagram in datagrams[: -1]:
				# These are complete messages
				self._databuffer.append(datagram)
			self._bufferhasitems.notify_all()
			self._bufferhasitems.release()

	def close(self):
		self._quit = True

	def run(self):
		while not self._quit:
			data = self._conn.read(128)
			if len(data) > 0:
				self._rxdata(data)
	
	def _write(self, string):
		self._debug("-> " + string)
		self._conn.write(string.encode("latin1"))

	def _writeslow(self, string):
		self._debug("-> Slow: " + string)
		b = string.encode("latin1")
		for i in range(len(b)):
			n = b[i : i + 1]
			self._conn.write(n)
			time.sleep(0.55)			# 0.4 is actually too fast, 0.5 works (0.55 is some safety margin)

	def settime(self, timestamp):
		assert(isinstance(timestamp, datetime.datetime))
		command = "t%02d%02d%02d%02d%02d%02d" % (timestamp.day, timestamp.month, timestamp.year - 2000, timestamp.hour, timestamp.minute, timestamp.second)
		self._writeslow(command)
		self._expectresponse("Datum und Zeit gestellt")

	def synctime(self):
		self.settime(datetime.datetime.now())

	def getversion(self):
		self._write("v")
		if self._nextmsg() is None:
			# Timeout, no response
			raise Exception("Timeout waiting for first datagram of version.")
		
		versionstr = self._nextmsg()
		if versionstr is None:
			# Timeout, no response
			raise Exception("Timeout waiting for second datagram of version.")

		result = { "Mode": None }
		if versionstr == "Standard":
			result["Mode"] = "Standard"
		elif GSConnection._version_pc_regex.match(versionstr):
			result["Mode"] = "PC"
			result["version"] = GSConnection._version_pc_regex[1]
			result["serial"] = int(GSConnection._version_pc_regex[2])
			result["buffill"] = int(GSConnection._version_pc_regex[3], 16)
			day = int(GSConnection._version_pc_regex[4])
			mon = int(GSConnection._version_pc_regex[5])
			year = int(GSConnection._version_pc_regex[6]) + 2000
			hour = int(GSConnection._version_pc_regex[7])
			mint = int(GSConnection._version_pc_regex[8])
			sec = int(GSConnection._version_pc_regex[9])
			result["datetime"] = datetime.datetime(year, mon, day, hour, mint, sec)
		else:
			raise Exception("Unparsable version string '%s'." % (versionstr))
		return result

	def switchmode(self, newmode):
		assert(newmode in [ "Standard", "PC" ])
		currentmode = self.getversion()["Mode"]
		if currentmode == newmode:
			# Already done!
			return

		if newmode == "Standard":
			# Switch to standard mode, end PC mode
			self._write("X")
			self._expectresponse("PC-Mode beendet", 2)
		elif newmode == "PC":
			# Switch to PC mode
			self._write("P")
			self._expectresponse("PC-Mode gestartet", 2)

	def _linechecksum(data):
		return sum(data[0 : -1]) & 0xff

	def readlog(self):
		self.switchmode("PC")
		buffill = self.getversion()["buffill"]
		self._write("b")
		self._expectresponse("GAMMA-SCOUT Protokoll")
	
		log = [ ]
		linecnt = 0
		while True:
			linecnt += 1
			nextmsg = self._nextmsg()
			if nextmsg is None:
				break
			if (len(nextmsg) % 2) != 0:
				raise Exception("Protocol line was not a multiple of two bytes (%d bytes received)." % (len(nextmsg)))

			logdata = [ int(nextmsg[2 * i : 2 * i + 2], 16) for i in range(len(nextmsg) // 2) ]
			calcchksum = GSConnection._linechecksum(logdata)
			if calcchksum != logdata[-1]:
				# Warn about this only, cannot do anything anyways
				print("Warning: Log line %d has checksum error, calculated 0x%x, transmitted 0x%x." % (calcchksum, logdata[-1]), file = sys.stderr)

			log += logdata[:-1]

		return (buffill, bytes(log))

	def clearlog(self):
		self.switchmode("PC")
		self._write("z")
		self._expectresponse("Protokollspeicher wieder frei")
	
	def devicereset(self):
		self.switchmode("PC")
		self._write("i")
	
	def readconfig(self):
		self.switchmode("PC")
		self._write("c")
		linecnt = 0
		result = { }
		log = [ ]
		while True:
			linecnt += 1
			nextmsg = self._nextmsg()
			if nextmsg is None:
				break
			if linecnt == 2:
				if not GSConnection._config_firstline_regex.match(nextmsg):
					raise Exception("First configuration data line format unexpected (received '%s')." % (nextmsg))
				log.append(int(GSConnection._config_firstline_regex[1], 16))
				log.append(int(GSConnection._config_firstline_regex[2], 16))
				nextmsg = GSConnection._config_firstline_regex[3]

			if linecnt >= 2:
				logdata = [ int(nextmsg[2 * i : 2 * i + 2], 16) for i in range(len(nextmsg) // 2) ]
				log += logdata
		return bytes(log)


