idf_component_register(SRCS "main.c" "power.c" "link.c" "radio.c" "cmds.c" "console.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES power_mgr esp_link gbwifi esp_wifi esp_netif esp_event nvs_flash esp_timer
                                     esp_adc esp_driver_gpio esp_driver_spi esp_driver_usb_serial_jtag esp_phy
                                     esp_http_client esp_https_ota app_update mbedtls esp_pm lwip)
