from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient
from datetime import datetime, timedelta
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

# Función de decodificación
def decode_smartone_solar_payload(hex_payload: str) -> dict:
    def decode_single_payload(payload_bytes: bytes) -> dict:
        if len(payload_bytes) != 9:
            return {"error": f"Invalid chunk size {len(payload_bytes)} bytes, expected 9 bytes."}

        byte0 = payload_bytes[0]
        global_message_type = byte0 & 0b00000011  # Bits 0 y 1
        battery_state = (byte0 >> 2) & 0b00000001  # Bit 2
        gps_data_valid = (byte0 >> 3) & 0b00000001  # Bit 3
        missed_input_1 = (byte0 >> 4) & 0b00000001  # Bit 4
        missed_input_2 = (byte0 >> 5) & 0b00000001  # Bit 5
        gps_fail_counter = (byte0 >> 6) & 0b00000011  # Bits 6 y 7

        decoded = {
            "Global Message Type": global_message_type,
            "Battery State": "Good battery" if battery_state == 0 else "Replace battery",
            "GPS Data Valid": "GPS Data valid in this message" if gps_data_valid == 0 else "GPS failed in this message cycle, ignore Latitude and Longitude fields",
            "Missed Input 1 State Change": "Yes" if missed_input_1 == 1 else "No",
            "Missed Input 2 State Change": "Yes" if missed_input_2 == 1 else "No",
            "GPS Fail Counter": gps_fail_counter,
        }

        if global_message_type == 0:
            # Latitude y Longitude
            latitude_raw = int.from_bytes(payload_bytes[1:4], byteorder='big', signed=True)
            longitude_raw = int.from_bytes(payload_bytes[4:7], byteorder='big', signed=True)
            latitude = latitude_raw * (90.0 / 8388608.0)
            longitude = longitude_raw * (180.0 / 8388608.0)

            byte7 = payload_bytes[7]
            input_1_change = (byte7 >> 0) & 0b00000001
            input_1_state = (byte7 >> 1) & 0b00000001
            input_2_change = (byte7 >> 2) & 0b00000001
            input_2_state = (byte7 >> 3) & 0b00000001
            message_sub_type = (byte7 >> 4) & 0b00001111

            decoded.update({
                "Latitude": latitude,
                "Longitude": longitude,
                "Input 1 Change": "Triggered message" if input_1_change == 1 else "Did not trigger message",
                "Input 1 State": "Open" if input_1_state == 1 else "Closed",
                "Input 2 Change": "Triggered message" if input_2_change == 1 else "Did not trigger message",
                "Input 2 State": "Open" if input_2_state == 1 else "Closed",
                "Message Sub-Type": message_sub_type_description(message_sub_type)
            })

            byte8 = payload_bytes[8]
            vibration_triggered_message = (byte8 >> 3) & 0b00000001
            vibration_bit = (byte8 >> 4) & 0b00000001
            two_d_fix = (byte8 >> 5) & 0b00000001
            motion = (byte8 >> 6) & 0b00000001
            fix_confidence_bit = (byte8 >> 7) & 0b00000001

            decoded.update({
                "Vibration Triggered Message": "No" if vibration_triggered_message == 0 else "Yes",
                "Vibration State": "Unit is not in a state of vibration" if vibration_bit == 0 else "Unit is in a state of vibration",
                "GPS Fix Type": "3D fix" if two_d_fix == 0 else "2D fix",
                "Motion State": "Device was At-Rest" if motion == 0 else "Device was In-Motion",
                "Fix Confidence": "High confidence in GPS fix accuracy" if fix_confidence_bit == 0 else "Reduced confidence in GPS fix accuracy",
            })
        else:
            decoded["payload_decoded"] = f"Decoding for Global Message Type {global_message_type} not implemented."

        return decoded

    # Función auxiliar para descripción de subtipos
    def message_sub_type_description(sub_type_value: int) -> str:
        subtypes = {
            0: "Location Message",
            1: "Device Turned on Message",
            2: "Change of Location Area alert message",
            3: "Input Status Changed message",
            4: "Undesired Input State message",
            5: "Re-Centering message",
            6: "Speed & Heading message"
        }
        return subtypes.get(sub_type_value, f"Unknown Sub-type ({sub_type_value})")

    # Convertir hex a bytes
    if hex_payload.startswith("0x") or hex_payload.startswith("0X"):
        payload_bytes = bytes.fromhex(hex_payload[2:])
    else:
        payload_bytes = bytes.fromhex(hex_payload)

    if not payload_bytes:
        return {"error": "Empty payload"}

    if len(payload_bytes) == 9:
        # Payload simple
        return decode_single_payload(payload_bytes)

    elif len(payload_bytes) > 9:
        # Payload multipart
        results = []
        for i in range(0, len(payload_bytes), 9):
            chunk = payload_bytes[i:i+9]
            if len(chunk) != 9:
                results.append({"error": f"Incomplete chunk of {len(chunk)} bytes, expected 9 bytes."})
                continue
            result = decode_single_payload(chunk)
            results.append(result)
        return {"multipart_payload_decoded": results}

    else:
        return {"error": f"Unexpected payload length {len(payload_bytes)} bytes. Expected 9 or multiples of 9 bytes."}
    
def formatear_fecha(time_stamp_raw: str):
    if not time_stamp_raw:
        return None
        
    try:
        fecha_dt = None
        
        # Caso 1: Formato con barras y GMT (09/10/2025 15:18:07 GMT)
        if "/" in time_stamp_raw:
            clean_date = time_stamp_raw.replace("GMT", "").strip()
            fecha_dt = datetime.strptime(clean_date, "%d/%m/%Y %H:%M:%S")
        
        # Caso 2: Formato ISO (2025-07-14T05:35:24.000-04:00)
        else:
            fecha_dt = datetime.fromisoformat(time_stamp_raw.replace('Z', '+00:00'))
            # Si el ISO ya trae zona horaria (offset), lo convertimos a naive (sin zona) 
            # para poder restarle las horas fácilmente si es necesario
            if fecha_dt.tzinfo is not None:
                fecha_dt = fecha_dt.replace(tzinfo=None)

        if fecha_dt:
            # APLICAR DESCUENTO DE 3 HORAS
            # Si recibes 13:40, esto lo dejará en 10:40
            fecha_ajustada = fecha_dt - timedelta(hours=3)
            return fecha_ajustada
            
    except Exception as e:
        print(f"Error procesando fecha {time_stamp_raw}: {e}")
        return None


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

        if not messages:
            messages = []
        elif isinstance(messages, dict):
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
            
            time_stamp_dt = formatear_fecha(time_stamp)
            
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
                    "payload_encoding": msg.get("payload", {}).get("@encoding") if isinstance(msg.get("payload"), dict) else None,
                    "time_stamp_dt": time_stamp_dt
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
                    "decoding_error": decoded_data["error"],
                    "time_stamp_dt": time_stamp_dt
                }
            
            collection.insert_one(doc)
            inserted_count += 1

        # Repuesta con mensaje
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