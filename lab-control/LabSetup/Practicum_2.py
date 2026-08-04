# Simple script enabling an interface with a Delta Elektonika power supply
# Communicates via a TCP socket with SCPI commands as found in the programming manual

# Import relevant Python libraries
import socket
import time
import numpy as np
from scipy.interpolate import interp1d
import matplotlib.pyplot as plt
import PID_controller
from scipy.interpolate import make_interp_spline

SUPPLY_IP = "192.168.0.130"     # IP address of your specific power supply
SUPPLY_PORT = 8462              # Communication port to use
BUFFER_SIZE = 128               # max msg size
TIMEOUT_SECONDS = 5             # return error if we dont hear from supply within 10 sec
MAX_VOLT = 30                  # default 10
MAX_CUR = 2.8                     # default 10
MAX_POW = 800 
validSrcList = ["front", "web", "seq", "eth", "slot1", "slot2", "slot3", "slot4", "loc", "rem"]


supplySocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)    # set up socket
supplySocket.connect((SUPPLY_IP, SUPPLY_PORT))                      # connect socket
supplySocket.settimeout(TIMEOUT_SECONDS)                            # define timeout

# Function for sending and receiving encoded messages with the power supply 
def sendAndReceiveCommand(msg):
    msg =  msg + "\n"
    supplySocket.sendall(msg.encode("UTF-8"))
    return supplySocket.recv(BUFFER_SIZE).decode("UTF-8").rstrip()


# Function to send encoded message to the power supply without waiting for a response
def sendCommand(msg):
    msg =  msg + "\n"
    supplySocket.sendall(msg.encode("UTF-8"))


def setRemoteShutdownState(state):
    if state:
        sendCommand("SYST:RSD 1")
    else:
        sendCommand("SYST:RSD 0")

# Function to set maximum operating power 
def setPower(power):
    retval = 0
    if power > 0 and power <= MAX_POW:
        sendCommand("SOUR:POWER {0}".format(power))
    else:
        retval = -1
    return retval
# 
# Function to set voltage 
def setVoltage(volt):
    volt = round(volt, 4)
    sendCommand(f"SOURCE:VOLTAGE {volt}")
    
    
# Function to set current 
def setCurrent(cur):
    cur = round(cur, 4)
    if cur > 0.0:
        sendCommand("SOURCE:CURRENT:NEGATIVE 0.0")
        sendCommand(f"SOURCE:CURRENT {cur}")
        
    elif cur < 0.0:
        sendCommand("SOURCE:CURRENT 0.0")
        sendCommand(f"SOURCE:CURRENT:NEGATIVE {cur}")
        
    else:
        sendCommand("SOURCE:CURRENT 0.0")
        sendCommand("SOURCE:CURRENT:NEGATIVE 0.0")

# Function to read voltage
def readVoltage():
    return sendAndReceiveCommand("SOUR:VOLT?")

# Function to read current
def readCurrent():
    return sendAndReceiveCommand("SOUR:CUR?")

# Power, voltage, and current are to be controlled via ethernet
def setProgSourceP(src):
    if src in validSrcList:
        sendCommand("SYST:REM:CP {0}".format(src))
        
def setProgSourceV(src):
    if src in validSrcList:
        sendCommand("SYST:REM:CV {0}".format(src))

def setProgSourceI(src):
    if src.lower() in validSrcList:
        sendCommand("SYST:REM:CC {0}".format(src))

def setOutputState(state):
    if state:
        sendCommand("OUTPUT 1")
    else:
        sendCommand("OUTPUT 0")

# Safely close the connection with power supply
def closeSocket():
    supplySocket.close()

# if __name__ == "__main__":
    
# print(sendAndReceiveCommand("*IDN?"))
# MAX_VOLT = float(sendAndReceiveCommand("SOUR:VOLT:MAX?"))
# MAX_CUR = float(sendAndReceiveCommand("SOUR:CUR:MAX?"))

# print(MAX_VOLT, MAX_CUR)

setProgSourceP("eth")
setPower(-15000)
setPower(15000)

setProgSourceV("eth")
setProgSourceI("eth")
setOutputState(True)

setVoltage(1)

 
# =============================================================================
# I-V curve
# Maximum and minimum values must stay as -3 and 0 for i_points
# Maximum and minimum values must stay as 0 and 60 for v_points
# You can change or add values between the maximum and minimum

i_points = np.array([-3, -1.5, 0])
v_points = np.array([0, 30, 60])

i_v_curve = interp1d(v_points,i_points)

# =============================================================================
# Your loop

### create lists to log data
set_current_list = []
meas_current_list = []
meas_voltage_list = []

test_duration = 0
T_meas = 0
test_start = time.perf_counter()
t_step = 0.5
sample_time = 0.05

I_feedback = -0.2
T_meas_PI = 0
PI_meas_list = [time.perf_counter()]
I_list = [I_feedback]
feedback_list = [I_feedback]
time_list = []
time_meas_list_1 = []
setpoint_list = []

# =============================================================================
# Proportional - Integral Controller

P = 1.5
I = 0.75
D = 0

pid = PID_controller.PID(P, I, D)
pid.SetPoint = 0

###
while test_duration < 20:
    delta_time = time.perf_counter() - T_meas
    delta_time_PI = time.perf_counter() - T_meas_PI
    
    ### Check if time passed is greater than the desired time step
    if delta_time >= t_step:
        T_meas = time.perf_counter()  
        
        ### Measure voltage across the power supply
        v_meas = float(sendAndReceiveCommand("MEASURE:VOLTAGE?"))
        i_meas = float(sendAndReceiveCommand("MEASURE:CURRENT?"))
         # print(f'Measured voltage: {round(float(v_meas),2)}')
         # print(f'Measured current: {round(float(i_meas),2)}')
        
        ### Determine and set the appropriate current at that voltage
        i_set = float(i_v_curve(v_meas))        
        pid.SetPoint = i_set
        I_feedback = i_meas
        setCurrent(i_set)
        print(f'Set current: {i_set}')
        
        ### Log data
        set_current_list.append(i_set)
        meas_current_list.append(i_meas)
        meas_voltage_list.append(v_meas)
        
    elif delta_time_PI >= sample_time:
        ### Log and plot values zsm in between current control steps
        T_i = time.perf_counter() - T_meas_PI
        # I_output = pid.output
        # if I_output != None:
        I_feedback += pid.update(I_feedback)     # Update PID instance with new measured I value
        # I_OG += pid.output    # iteratively sum output to I        
        T_meas_PI = time.perf_counter()  
        
        setCurrent(I_feedback)
        # print(f'Set current: {i_set}')
        
        feedback_list.append(I_feedback)
        setpoint_list.append(pid.SetPoint) 
        time_list.append(time.perf_counter() - test_start)
        time_meas_list_1.append(T_i)
            
    test_duration = time.perf_counter() - test_start

# =============================================================================
# Safely reset and disconnect from power supply
setVoltage(0)
setCurrent(0)

setProgSourceV("front") #JWa toegevoegd voor handcontrole
setProgSourceI("front") #JWa toegevoegd voor handcontrole
   
setRemoteShutdownState(0) # RSD Enabled = supply off/disabled
print(readVoltage())
print(readCurrent())
closeSocket()

# =============================================================================
# Plot the logged data and the I-V curve
x_new = np.linspace(0,60,100)
y_new = i_v_curve(x_new)

### Plot I-V
plt.scatter(x=meas_voltage_list, y=meas_current_list)
plt.plot(x_new, y_new)
# =============================================================================

time_sm = np.array(time_list)
time_smooth = np.linspace(time_sm.min(), time_sm.max(), 3000)
del feedback_list[0]

helper_x3 = make_interp_spline(time_list, feedback_list)
feedback_smooth = helper_x3(time_smooth)

plt.figure()
plt.plot(time_list, feedback_list)
plt.plot(time_list, setpoint_list)
plt.xlim((0, test_duration))
# plt.ylim((min(feedback_list)-0.5, max(feedback_list)+2))
plt.xlabel('time (s)')
plt.ylabel('PID (PV)')
plt.title(f'TEST PID - Kp:{P}, Ki:{I}, Kd:{D}')
