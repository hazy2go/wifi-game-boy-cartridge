idf_component_register(SRCS "rf_logger.c"
                       PRIV_REQUIRES esp_wifi esp_event esp_netif nvs_flash esp_timer lwip)
