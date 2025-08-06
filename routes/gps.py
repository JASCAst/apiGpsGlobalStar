from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient
from datetime import datetime
from dotenv import load_dotenv
import xmltodict
import os
import xml.etree.ElementTree as ET

load_dotenv()

# Configura tu conexión a MongoDB
client = MongoClient(os.getenv("MONGODB_URI"))
db = client["globalStar"]
collection = db["datos"]

router = APIRouter()

# Función de decodificación (Copia y pega tu función aquí)
def decode_smartone_solar_payload(hex_payload: str) -> dict:
    """
    Decodes a SmartOne Solar hex payload based on the Type 0 message class.
    Assumes the input hex_payload is a string like '0x04C5080DCC190A0000'.
    """
    if hex_payload.startswith("0x"):
        payload_bytes = bytes.fromhex(hex_payload[2:])
    else:
        payload_bytes = bytes.fromhex(hex_payload)

    if not payload_bytes or len(payload_bytes) != 9:
        return {"error": "Invalid or incomplete payload. Expected 9 bytes for Type 0 message."}

    # Byte 0: Global Message Type, Battery State, GPS Data Valid, Missed Input State Change, GPS Fail Counter
    byte0 = payload_bytes[0]
    global_message_type = byte0 & 0b00000011  # Bits 0 and 1
    battery_state = (byte0 >> 2) & 0b00000001  # Bit 2
    gps_data_valid = (byte0 >> 3) & 0b00000001  # Bit 3
    missed_input_1 = (byte0 >> 4) & 0b00000001  # Bit 4
    missed_input_2 = (byte0 >> 5) & 0b00000001  # Bit 5
    gps_fail_counter = (byte0 >> 6) & 0b00000011  # Bits 6 and 7

    decoded_data = {
        "Global Message Type": global_message_type,
        "Battery State": "Good battery" if battery_state == 0 else "Replace battery",
        "GPS Data Valid": "GPS Data valid in this message" if gps_data_valid == 0 else "GPS failed in this message cycle, ignore Latitude and Longitude fields",
        "Missed Input 1 State Change": "Yes" if missed_input_1 == 1 else "No",
        "Missed Input 2 State Change": "Yes" if missed_input_2 == 1 else "No",
        "GPS Fail Counter": gps_fail_counter,
    }

    if global_message_type == 0:  # Type 0 - Standard Message
        # Bytes 1-6: Latitude/Longitude (48 bits)
        latitude_raw = int.from_bytes(payload_bytes[1:4], byteorder='big', signed=True)
        longitude_raw = int.from_bytes(payload_bytes[4:7], byteorder='big', signed=True)
        latitude = latitude_raw * (90.0 / 8388608.0)
        longitude = longitude_raw * (180.0 / 8388608.0)

        # Byte 7: Input Status and Message Sub-type
        byte7 = payload_bytes[7]
        input_1_change = (byte7 >> 0) & 0b00000001
        input_1_state = (byte7 >> 1) & 0b00000001
        input_2_change = (byte7 >> 2) & 0b00000001
        input_2_state = (byte7 >> 3) & 0b00000001
        message_sub_type = (byte7 >> 4) & 0b00001111

        decoded_data["Latitude"] = latitude
        decoded_data["Longitude"] = longitude
        decoded_data["Input 1 Change"] = "Triggered message" if input_1_change == 1 else "Did not trigger message"
        decoded_data["Input 1 State"] = "Open" if input_1_state == 1 else "Closed"
        decoded_data["Input 2 Change"] = "Triggered message" if input_2_change == 1 else "Did not trigger message"
        decoded_data["Input 2 State"] = "Open" if input_2_state == 1 else "Closed"
        decoded_data["Message Sub-Type"] = message_sub_type_description(message_sub_type)

        # Byte 8: Reserved, Vibration Triggered Message, Vibration Bit, 2D/3D fix, Motion, Fix Confidence Bit
        byte8 = payload_bytes[8]
        vibration_triggered_message = (byte8 >> 3) & 0b00000001
        vibration_bit = (byte8 >> 4) & 0b00000001
        two_d_fix = (byte8 >> 5) & 0b00000001
        motion = (byte8 >> 6) & 0b00000001
        fix_confidence_bit = (byte8 >> 7) & 0b00000001

        decoded_data["Vibration Triggered Message"] = "No" if vibration_triggered_message == 0 else "Yes"
        decoded_data["Vibration State"] = "Unit is not in a state of vibration" if vibration_bit == 0 else "Unit is in a state of vibration"
        decoded_data["GPS Fix Type"] = "3D fix" if two_d_fix == 0 else "2D fix"
        decoded_data["Motion State"] = "Device was At-Rest" if motion == 0 else "Device was In-Motion"
        decoded_data["Fix Confidence"] = "High confidence in GPS fix accuracy" if fix_confidence_bit == 0 else "Reduced confidence in GPS fix accuracy"
    else:
        decoded_data["payload_decoded"] = f"Decoding for Global Message Type {global_message_type} is not fully implemented yet for other bytes."

    return decoded_data

def message_sub_type_description(sub_type_value: int) -> str:
    """Returns the description for Type 0 Message Class Sub-types."""
    if sub_type_value == 0:
        return "Location Message"
    elif sub_type_value == 1:
        return "Device Turned on Message"
    elif sub_type_value == 2:
        return "Change of Location Area alert message"
    elif sub_type_value == 3:
        return "Input Status Changed message"
    elif sub_type_value == 4:
        return "Undesired Input State message"
    elif sub_type_value == 5:
        return "Re-Centering message"
    elif sub_type_value == 6:
        return "Speed & Heading message"
    else:
        return f"Unknown Sub-type ({sub_type_value})"

# Recibir datos de la API
@router.post("/gpsApi", response_class=PlainTextResponse)
async def receive_stu_messages(request: Request):
    xml_data = await request.body()
    try:
        parsed = xmltodict.parse(xml_data)
        stu_messages = parsed.get("stuMessages", {})
        message_id = stu_messages.get("@messageID")
        time_stamp = stu_messages.get("@timeStamp")
        messages = stu_messages.get("stuMessage", [])

        if isinstance(messages, dict):
            messages = [messages]

        inserted_count = 0
        for msg in messages:
            # Extraer el payload sin decodificar
            raw_payload = msg.get("payload", {}).get("#text") if isinstance(msg.get("payload"), dict) else msg.get("payload")

            # Decodificar el payload usando tu función
            decoded_data = {}
            if raw_payload:
                decoded_data = decode_smartone_solar_payload(raw_payload)

            # Si la decodificación no falló, actualiza el diccionario con los datos decodificados
            if "error" not in decoded_data:
                doc = {
                    "message_id": message_id,
                    "time_stamp": time_stamp,
                    "esn": msg.get("esn"),
                    "unixTime": msg.get("unixTime"),
                    "gps": msg.get("gps"),
                    "payload_raw": raw_payload, # Guardamos el payload original para referencia
                    **decoded_data,  # Añadimos todos los campos decodificados
                    "payload_length": msg.get("payload", {}).get("@length") if isinstance(msg.get("payload"), dict) else None,
                    "payload_source": msg.get("payload", {}).get("@source") if isinstance(msg.get("payload"), dict) else None,
                    "payload_encoding": msg.get("payload", {}).get("@encoding") if isinstance(msg.get("payload"), dict) else None
                }
            else:
                # En caso de error de decodificación, guardamos el error y el payload original
                doc = {
                    "message_id": message_id,
                    "time_stamp": time_stamp,
                    "esn": msg.get("esn"),
                    "unixTime": msg.get("unixTime"),
                    "gps": msg.get("gps"),
                    "payload_raw": raw_payload,
                    "decoding_error": decoded_data["error"]
                }
            
            collection.insert_one(doc)
            inserted_count += 1

        # El resto del código de respuesta permanece igual
        delivery_time = datetime.utcnow().strftime("%d/%m/%Y %H:%M:%S GMT")
        correlation_id = message_id or "unknown"
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
                        <stuResponseMsg xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                        xsi:noNamespaceSchemaLocation="http://cody.glpconnect.com/XSD/StuResponse_Rev1_0.xsd"
                        deliveryTimeStamp="{delivery_time}" messageID="{message_id}" correlationID="{correlation_id}">
                        <state>pass</state>
                        <stateMessage>{inserted_count} messages received and stored successfully</stateMessage>
                        </stuResponseMsg>
                        """
        return Response(content=response_xml, media_type="text/xml")

    except Exception as e:
        error_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
                    <stuResponseMsg xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                    xsi:noNamespaceSchemaLocation="http://cody.glpconnect.com/XSD/StuResponse_Rev1_0.xsd"
                    deliveryTimeStamp="{datetime.utcnow().strftime("%d/%m/%Y %H:%M:%S GMT")}" messageID="unknown" correlationID="unknown">
                    <state>fail</state>
                    <stateMessage>{str(e)}</stateMessage>
                    </stuResponseMsg>
                    """
        return Response(content=error_xml, media_type="text/xml", status_code=400)