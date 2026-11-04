from __future__ import annotations

import re


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "subtitle": "P2P traffic atlas",
        "overview": "Overview",
        "destinations": "Destinations",
        "history": "History",
        "geoip_settings": "GeoIP database",
        "theme": "Theme",
        "language": "Language",
        "system": "System default",
        "geoip_dialog_intro": "Select one or more local MMDB databases. City, Country, ASN and ISP files can be combined.",
        "geoip_privacy_note": "Databases are read locally. Peer IP addresses are not sent to an online geolocation service.",
        "geoip_add": "Add databases…",
        "geoip_remove": "Remove selected",
        "geoip_empty": "No databases selected",
        "provider": "Network / ASN",
        "process": "Process",
        "native_engine_active": "Rust monitoring core active",
        "native_engine_fallback": "Rust core not built · Python compatibility mode",
        "preparing": "Preparing local GeoIP databases…",
        "dark": "Dark",
        "light": "Light",
        "russian": "Русский",
        "english": "English",
        "overview_title": "Overview",
        "tagline": "Your network activity, kept local.",
        "auto_interface": "Auto interface",
        "read_only": "READ-ONLY",
        "starting": "●  Starting monitor",
        "monitoring": "●  Monitoring · {count} endpoints",
        "uploaded": "UPLOADED",
        "downloaded": "DOWNLOADED",
        "live_transfer": "LIVE TRANSFER",
        "since_started": "Since monitoring started",
        "selected_saved": "Selected interface · saved locally",
        "world_activity": "World activity",
        "approx_ip": "Approximate IP locations",
        "map_empty": "Choose a local GeoIP database to locate active endpoints",
        "top_countries": "Top countries",
        "active_not_volume": "Active public endpoints · not traffic volume",
        "country": "Country",
        "region": "Region",
        "top_regions": "Top regions",
        "expand_map": "Enlarge map",
        "map_zoom_in": "Zoom in",
        "map_zoom_out": "Zoom out",
        "map_reset": "Reset view",
        "map_country_region_subtitle": "Countries · regions · approximate IP locations",
        "map_attribution": "Geolocation data: DB-IP.com (CC BY 4.0) · map boundaries: Natural Earth (public domain).",
        "peers": "Peers",
        "traffic_history": "Traffic history",
        "last_hour": "Last hour",
        "history_empty": "Traffic history will appear as data is collected",
        "recent_destinations": "Recent destinations",
        "ip_address": "IP address",
        "location": "Location",
        "type": "Type",
        "data_note": "Read-only monitoring: interface totals are exact; destination byte attribution is not available yet.",
        "data_note_live": "Read-only: exact interface totals; endpoint locations are approximate. Bytes per IP/country are not attributed yet.",
        "unknown": "Unknown",
        "geoip_missing": "GeoIP not configured",
        "page_coming": "{page} view is part of the next prototype milestone. The live overview is available now.",
        "geoip_title": "Choose one or more local GeoIP databases",
        "geoip_filter": "MaxMind database (*.mmdb)",
        "geoip_saved": "GeoIP database saved",
        "geoip_restart": "The local database will be loaded when SwarmAtlas is restarted.",
        "stopping_title": "SwarmAtlas is still stopping",
        "stopping_message": "The read-only monitor has not finished its current OS query. Close the window again in a moment.",
        "status_no_interface": "No active network interface found",
        "status_waiting": "Waiting for first network sample",
        "status_socket_denied": "Socket table access restricted; interface totals still work",
        "status_geoip_missing": "GeoIP database not configured",
        "status_geoip_install": "Install the optional 'geoip' dependency",
        "status_geoip_open": "Could not open GeoIP database: {detail}",
        "status_local_geoip": "Local GeoIP: {name}",
        "status_geoip_ready": "Local GeoIP databases prepared: {detail}",
        "status_geoip_invalid": "No usable GeoIP databases: {detail}",
        "status_endpoints": "{count} public endpoints observed (scan every 15 s)",
        "status_interface_error": "Interface counters unavailable: {detail}",
        "status_socket_error": "Socket table unavailable: {detail}",
        "status_scan_error": "Endpoint scan unavailable: {detail}",
        "status_stopped": "Monitor stopped safely: {detail}",
        "theme_label_dark": "Dark theme",
        "theme_label_light": "Light theme",
    },
    "ru": {
        "subtitle": "Атлас P2P-трафика",
        "overview": "Обзор",
        "destinations": "Назначения",
        "history": "История",
        "geoip_settings": "База GeoIP",
        "theme": "Тема",
        "language": "Язык",
        "system": "Как в системе",
        "geoip_dialog_intro": "Добавьте одну или несколько локальных баз MMDB. Можно объединять City, Country, ASN и ISP.",
        "geoip_privacy_note": "Базы читаются локально. IP-адреса пиров не отправляются сторонним геосервисам.",
        "geoip_add": "Добавить базы…",
        "geoip_remove": "Удалить выбранные",
        "geoip_empty": "Базы не выбраны",
        "provider": "Сеть / ASN",
        "process": "Процесс",
        "native_engine_active": "Работает ядро мониторинга Rust",
        "native_engine_fallback": "Ядро Rust не собрано · режим совместимости Python",
        "preparing": "Подготовка локальных баз GeoIP…",
        "dark": "Тёмная",
        "light": "Светлая",
        "russian": "Русский",
        "english": "English",
        "overview_title": "Обзор",
        "tagline": "Сетевая активность остаётся на этом компьютере.",
        "auto_interface": "Автовыбор интерфейса",
        "read_only": "ТОЛЬКО ЧТЕНИЕ",
        "starting": "●  Запуск мониторинга",
        "monitoring": "●  Мониторинг · узлов: {count}",
        "uploaded": "ОТПРАВЛЕНО",
        "downloaded": "ПОЛУЧЕНО",
        "live_transfer": "ТЕКУЩАЯ СКОРОСТЬ",
        "since_started": "С начала наблюдения",
        "selected_saved": "Выбранный интерфейс · сохранено локально",
        "world_activity": "Активность в мире",
        "approx_ip": "Примерное расположение IP",
        "map_empty": "Выберите локальную базу GeoIP для отображения узлов",
        "top_countries": "Популярные страны",
        "active_not_volume": "Активные публичные адреса · не объём трафика",
        "country": "Страна",
        "region": "Регион",
        "top_regions": "Популярные регионы",
        "expand_map": "Увеличить карту",
        "map_zoom_in": "Приблизить",
        "map_zoom_out": "Отдалить",
        "map_reset": "Сбросить масштаб",
        "map_country_region_subtitle": "Страны · регионы · примерное расположение IP",
        "map_attribution": "Геолокация: DB-IP.com (CC BY 4.0) · границы карты: Natural Earth (общественное достояние).",
        "peers": "Узлы",
        "traffic_history": "История трафика",
        "last_hour": "Последний час",
        "history_empty": "График появится по мере сбора данных",
        "recent_destinations": "Последние назначения",
        "ip_address": "IP-адрес",
        "location": "Расположение",
        "type": "Тип",
        "data_note": "Мониторинг только для чтения: счётчики интерфейса точны, но объём трафика по адресам пока не определяется.",
        "data_note_live": "Только чтение: счётчики интерфейса точны, расположение узлов приблизительное. Объём трафика по IP и странам пока не определяется.",
        "unknown": "Неизвестно",
        "geoip_missing": "База GeoIP не настроена",
        "page_coming": "Раздел «{page}» появится в следующей версии прототипа. Сейчас доступен обзор сети.",
        "geoip_title": "Выберите одну или несколько локальных баз GeoIP",
        "geoip_filter": "База MaxMind (*.mmdb)",
        "geoip_saved": "База GeoIP сохранена",
        "geoip_restart": "Локальная база будет загружена после перезапуска SwarmAtlas.",
        "stopping_title": "SwarmAtlas ещё завершает работу",
        "stopping_message": "Монитор ещё не закончил текущий запрос к системе. Закройте окно немного позже.",
        "status_no_interface": "Активный сетевой интерфейс не найден",
        "status_waiting": "Ожидание первых данных сети",
        "status_socket_denied": "Доступ к таблице соединений ограничен; счётчики интерфейса продолжают работать",
        "status_geoip_missing": "База GeoIP не настроена",
        "status_geoip_install": "Установите дополнительную зависимость «geoip»",
        "status_geoip_open": "Не удалось открыть базу GeoIP: {detail}",
        "status_local_geoip": "Локальная база GeoIP: {name}",
        "status_geoip_ready": "Подготовлены локальные базы GeoIP: {detail}",
        "status_geoip_invalid": "Не удалось загрузить базы GeoIP: {detail}",
        "status_endpoints": "Обнаружено публичных узлов: {count} (сканирование каждые 15 с)",
        "status_interface_error": "Счётчики интерфейса недоступны: {detail}",
        "status_socket_error": "Таблица соединений недоступна: {detail}",
        "status_scan_error": "Не удалось просканировать назначения: {detail}",
        "status_stopped": "Монитор безопасно остановлен: {detail}",
        "theme_label_dark": "Тёмная тема",
        "theme_label_light": "Светлая тема",
    },
}


def tr(key: str, language: str = "ru", **values: object) -> str:
    selected = TRANSLATIONS.get(language, TRANSLATIONS["ru"])
    template = selected.get(key, TRANSLATIONS["en"].get(key, key))
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return template


def translate_status(status: str, language: str = "ru") -> str:
    if language != "ru":
        return status

    exact = {
        "No active network interface found": "status_no_interface",
        "Waiting for first network sample": "status_waiting",
        "Socket table access restricted; interface totals still work": "status_socket_denied",
        "GeoIP database not configured": "status_geoip_missing",
        "Install the optional 'geoip' dependency": "status_geoip_install",
    }
    key = exact.get(status)
    if key:
        return tr(key, language)

    patterns = (
        (r"^Local GeoIP: (.+)$", "status_local_geoip", "name"),
        (r"^Could not open GeoIP database: (.*)$", "status_geoip_open", "detail"),
        (r"^Interface counters unavailable: (.*)$", "status_interface_error", "detail"),
        (r"^Socket table unavailable: (.*)$", "status_socket_error", "detail"),
        (r"^Endpoint scan unavailable: (.*)$", "status_scan_error", "detail"),
        (r"^Monitor stopped safely: (.*)$", "status_stopped", "detail"),
    )
    for pattern, translation_key, field in patterns:
        match = re.match(pattern, status)
        if match:
            return tr(translation_key, language, **{field: match.group(1)})

    match = re.match(r"^No usable GeoIP databases: (.*)$", status)
    if match:
        return tr("status_geoip_invalid", language, detail=match.group(1))

    match = re.match(r"^Local GeoIP ready: (.*?)(?:; skipped (\d+))?$", status)
    if match:
        detail = match.group(1)
        skipped = match.group(2)
        if skipped:
            detail += f"; skipped: {skipped}"
        return tr("status_geoip_ready", language, detail=detail)

    match = re.match(r"^(\d+) public endpoints observed \(scan every 15 s\)$", status)
    if match:
        return tr("status_endpoints", language, count=match.group(1))

    return status
