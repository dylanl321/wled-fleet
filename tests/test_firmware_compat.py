from app.firmware import check_compat, detect_image_family, score_filename


def esp_image(chip_id: int) -> bytes:
    data = bytearray(32)
    data[0] = 0xE9
    data[1] = 4
    data[2] = 2
    data[8] = 0xEE
    data[12:14] = chip_id.to_bytes(2, "little")
    return bytes(data)


def esp8266_image() -> bytes:
    data = bytearray(32)
    data[0] = 0xE9
    data[1] = 3
    data[2] = 2
    data[8] = 0x40
    return bytes(data)


def test_header_detects_esp32_family():
    assert detect_image_family(esp_image(0x0000)) == "esp32"
    assert detect_image_family(esp_image(0x0009)) == "esp32s3"
    assert detect_image_family(esp8266_image()) is None


def test_esp32_bin_matches_esp32_and_not_esp8266():
    data = esp_image(0)
    ok = check_compat("esp32", data, "WLED_0.15.0_ESP32.bin")
    assert ok.ok
    assert ok.bin_family == "esp32"
    blocked = check_compat("esp8266", data, "WLED_0.15.0_ESP32.bin")
    assert not blocked.ok
    assert "esp32" in blocked.reason


def test_s3_image_rejected_for_plain_esp32():
    result = check_compat("esp32", esp_image(0x0009), "WLED_0.15.0_ESP32-S3.bin")
    assert not result.ok


def test_filename_and_header_conflict():
    result = check_compat("esp8266", esp_image(0), "WLED_0.14.4_ESP8266.bin")
    assert not result.ok
    assert "header" in result.reason


def test_esp8266_filename_matches_esp8266_device():
    result = check_compat("ESP8266", esp8266_image(), "WLED_0.14.4_ESP8266.bin")
    assert result.ok
    assert result.bin_family == "esp8266"


def test_board_mismatch_blocks_same_chip():
    result = check_compat(
        "esp32",
        esp_image(0),
        "WLED_16.0.1_Dig-Quad-V3.bin",
        release_name="Dig-Uno-V3",
        board="dig-uno",
    )
    assert not result.ok
    assert "dig-quad" in result.reason
    assert "dig-uno" in result.reason


def test_generic_bin_warns_on_a_board_profile():
    result = check_compat(
        "esp32",
        esp_image(0),
        "WLED_0.15.1_ESP32.bin",
        board="dig-uno",
    )
    assert result.ok
    assert any("dig-uno" in warning for warning in result.warnings)


def test_quinled_asset_scores_above_generic_and_other_boards():
    dig = score_filename("WLED_16.0.1_Dig-Uno-V3.bin", "esp32", "Dig-Uno-V3", "dig-uno")
    generic = score_filename("WLED_16.0.1_ESP32.bin", "esp32", "Dig-Uno-V3", "dig-uno")
    quad = score_filename("WLED_16.0.1_Dig-Quad-V3.bin", "esp32", "Dig-Uno-V3", "dig-uno")
    esp8266 = score_filename("WLED_0.14.4_ESP8266.bin", "esp32", None, None)
    assert dig > generic
    assert quad < 0
    assert esp8266 < 0
    assert generic > 0
