from typing import Optional, Dict, Union
import serial

class AD4826AController:
    def __init__(self, port: str = "COM3", baudrate: int = 9600, timeout: float = 1.0) -> None:
        """
        コンストラクタ：シリアルポートをオープンする
        """
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout
        )

    def close(self) -> None:
        """シリアルポートをクローズ"""
        if self.ser and self.ser.is_open:
            self.ser.close()

    def parse_header(self, header_byte: int) -> str:
        """
        レスポンス先頭バイト(ヘッダ)を解析して文字列を返す。
        STX(0x02) / ACK(0x06) / NAK(0x15) / UNKNOWN
        """
        if header_byte == 0x02:
            return "STX"
        elif header_byte == 0x06:
            return "ACK"
        elif header_byte == 0x15:
            return "NAK"
        else:
            return f"UNKNOWN(0x{header_byte:02X})"

    def build_command_frame(self, 
                            unit_no: str, 
                            channel_no: str, 
                            cmd_code: str, 
                            text: str = "") -> bytes:
        """
        AD-4826A 用のコマンドフレームを生成 (ENQ + ユニットNo + チャンネルNo + コマンドコード + テキスト + CRLF)
        """
        if len(cmd_code) < 8:
            cmd_code = cmd_code.ljust(8, '_')
        elif len(cmd_code) > 8:
            raise ValueError("コマンドコードは最大8文字までにしてください。")

        frame = bytearray()
        frame.append(0x05)  # ENQ (0x05)
        frame += unit_no.encode('ascii')
        frame += channel_no.encode('ascii')
        frame += cmd_code.encode('ascii')
        if text:
            frame += text.encode('ascii')
        frame += b'\r\n'
        return bytes(frame)

    def parse_response_frame(self, resp_bytes: bytes) -> Optional[Dict[str, Union[str, int, None]]]:
        """
        レスポンスを解析して dict 形式で返す。
        STX/ACKなら成功、NAKならエラーコードあり。
        """
        if len(resp_bytes) < 15:  # ヘッダ(1) + ユニット(2) +チャネル(2)+コマンド(8)+CRLF(2)=15
            return None

        header_byte = resp_bytes[0]
        header_str = self.parse_header(header_byte)

        unit_no = resp_bytes[1:3].decode('ascii', errors='ignore')
        channel_no = resp_bytes[3:5].decode('ascii', errors='ignore')
        cmd_code = resp_bytes[5:13].decode('ascii', errors='ignore')

        if header_byte == 0x15:
            # NAK(0x15)
            if len(resp_bytes) < (13 + 4):
                return None
            error_code = resp_bytes[13:15].decode('ascii', errors='ignore')
            return {
                "header_byte": header_byte,
                "header_str": header_str,
                "unit_no": unit_no,
                "channel_no": channel_no,
                "cmd_code": cmd_code,
                "error_code": error_code,
                "text_data": "",
            }
        else:
            # STX/ACK/UNKNOWN
            text_part = resp_bytes[13:-2].decode('ascii', errors='ignore')
            return {
                "header_byte": header_byte,
                "header_str": header_str,
                "unit_no": unit_no,
                "channel_no": channel_no,
                "cmd_code": cmd_code,
                "error_code": None,
                "text_data": text_part,
            }

    def send_command(self, 
                     unit_no: str, 
                     channel_no: str, 
                     cmd_code: str, 
                     text: str = "") -> Optional[Dict[str, Union[str, int, None]]]:
        """
        コマンドを送信してレスポンスを受信・解析。
        受信内容をprintし、解析結果を返す。
        """
        frame = self.build_command_frame(unit_no, channel_no, cmd_code, text)
        self.ser.write(frame)

        resp_bytes = self.ser.read_until(b'\r\n')
        if not resp_bytes:
            print(f"[send_command] No response (timeout). cmd={cmd_code}")
            return None

        print(f"[send_command] Raw response: {resp_bytes}")
        parsed = self.parse_response_frame(resp_bytes)
        if parsed is None:
            print("[send_command] Parse error.")
            return None
        else:
            print(f"[send_command] Parsed: {parsed}")
        return parsed

    # ----------------------------------------------------------------
    # ここから、ユーザの要望に応じた新しい機能を追加
    # 1) 現在の重量を取り出す関数
    # 2) 指定量を切り出す関数
    # 3) すべてを排出する関数
    # ----------------------------------------------------------------

    def get_current_weight(self, unit_no: str, channel_no: str) -> Optional[float]:
        """
        現在の重量(総重量)を取得する関数。
        - コマンド: GROSS___ (バッチモード時のみ有効のケースが多い)
        - 返り値: 浮動小数 (パースエラー時やNAK時は None)
        """
        resp = self.send_command(unit_no, channel_no, "GROSS___")
        if not resp or resp["header_str"] == "NAK":
            return None
        
        text_data = resp["text_data"]
        try:
            weight = float(text_data)
            return weight
        except ValueError:
            return None

    def cut_out_amount(self, unit_no: str, channel_no: str, amount: float) -> bool:
        """
        指定量を切り出す関数。
        例: FFコマンドで指定量を設定後、CFW_____で切出し開始 (バッチモード時のみ有効)
        
        :param amount: 切出し量 (ex: 100.0)
        :return: Trueならコマンド受付成功
        """
        # 1) FFコマンド(充填量データの書き込み)
        text_value = f"+{amount:010.3f}"  # "+00000100.000" のような書式
        resp = self.send_command(unit_no, channel_no, "FF______", text_value)
        if not resp or resp["header_str"] == "NAK":
            print("[cut_out_amount] FF write failed.")
            return False
        
        # 2) CFWコマンド(切出し開始)
        resp2 = self.send_command(unit_no, channel_no, "CFW_____")
        if not resp2 or resp2["header_str"] == "NAK":
            print("[cut_out_amount] CFW command failed.")
            return False

        print(f"[cut_out_amount] Started cutout with amount={amount}")
        return True

    def discharge_all(self, unit_no: str, channel_no: str) -> bool:
        """
        すべてを排出する関数。
        例: FDIS____ コマンド (強制排出) - バッチモード時のみ有効

        :return: True=コマンド受理, False=NAKまたは受信エラー
        """
        resp = self.send_command(unit_no, channel_no, "FDIS____")
        if not resp or resp["header_str"] == "NAK":
            print("[discharge_all] FDIS command failed.")
            return False
        
        print("[discharge_all] Forced discharge command accepted.")
        return True


# --- 使用例 ---
if __name__ == "__main__":
    controller = AD4826AController(port="COM3", baudrate=9600, timeout=1.0)
    try:
        # 1) 現在の重量を読み出し
        weight = controller.get_current_weight("00", "00")
        print("Current Weight:", weight)

        # 2) 指定量(例: 120.0)を切り出し
        success = controller.cut_out_amount("00", "00", 120.0)
        print("Cut out success?", success)

        # 3) 全て強制排出
        ok = controller.discharge_all("00", "00")
        print("Discharge all success?", ok)

    finally:
        controller.close()





