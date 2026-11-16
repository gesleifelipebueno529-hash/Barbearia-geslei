import argparse
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit


DATA_DIR = Path(os.environ.get(
    "DATA_DIR",
    str(Path(__file__).resolve().parent)
))
DATABASE_PATH = DATA_DIR / "clientes.db"

BUSINESS_WHATSAPP = "5515988376913"
SLOT_INTERVAL_MINUTES = 15
SERVICES = {
    "Cabelo": {"price": 25.0, "duration": 30},
    "Barba": {"price": 25.0, "duration": 20},
    "Sobrancelha": {"price": 10.0, "duration": 15},
    "Combo": {"price": 60.0, "duration": 50},
}

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("reservas_web")


class InvalidBooking(ValueError):
    pass


class SlotUnavailable(InvalidBooking):
    pass


def create_admin_appointment(payload):
    if not isinstance(payload, dict):
        raise InvalidBooking("Dados do agendamento inválidos.")

    client_name = str(payload.get("client", "")).strip()
    service_name = payload.get("service")
    date_text = payload.get("date")
    time_text = payload.get("time")

    if not client_name or len(client_name) > 100:
        raise InvalidBooking("Informe o nome do cliente.")
    if service_name not in SERVICES:
        raise InvalidBooking("Selecione um serviço válido.")
    if not isinstance(date_text, str) or not isinstance(time_text, str):
        raise InvalidBooking("Informe uma data e um horário válidos.")

    selected_date = parse_date(date_text)
    if selected_date is None or selected_date < date.today():
        raise InvalidBooking("Escolha uma data de hoje em diante.")
    if not re.fullmatch(r"\d{2}:\d{2}", time_text):
        raise InvalidBooking("Escolha um horário disponível.")

    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    try:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        if time_text not in available_slots(cursor, selected_date, service_name):
            raise SlotUnavailable(
                "Esse horário não está mais disponível. Atualize a agenda."
            )

        service = SERVICES[service_name]
        cursor.execute("""
        INSERT INTO agendamentos
        (data, hora, cliente, servico, valor, pago, duracao)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (
            selected_date.isoformat(),
            time_text,
            client_name,
            service_name,
            service["price"],
            service["duration"],
        ))
        appointment_id = cursor.lastrowid
        connection.commit()

    except (InvalidBooking, sqlite3.Error):
        connection.rollback()
        raise
    finally:
        connection.close()

    return appointment_id


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#11110f">
  <title>Agende seu horário | Barbearia Geslei</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #11110f;
      --panel: #1b1b18;
      --line: #39382f;
      --text: #f5f1e7;
      --muted: #bbb6a9;
      --gold: #d7ad62;
      --gold-dark: #9c7537;
      --error: #ffb4a9;
      font-family: Inter, "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(ellipse at 50% -20%, #5b4727 0, transparent 48%),
        var(--bg);
    }
    main { width: min(100% - 28px, 680px); margin: 0 auto; padding: 38px 0 56px; }
    header { text-align: center; margin-bottom: 26px; }
    .mark {
      display: inline-grid; place-items: center; width: 48px; height: 48px;
      border: 1px solid var(--gold); border-radius: 50%; color: var(--gold);
      font-family: Georgia, serif; font-size: 19px; letter-spacing: 1px;
    }
    .eyebrow { color: var(--gold); text-transform: uppercase; letter-spacing: 2px; font-size: 12px; }
    h1 { margin: 13px 0 8px; font-family: Georgia, serif; font-size: clamp(30px, 7vw, 44px); font-weight: 500; }
    header p { color: var(--muted); line-height: 1.55; margin: 0; }
    .card {
      background: color-mix(in srgb, var(--panel) 95%, transparent);
      border: 1px solid var(--line); border-radius: 18px; padding: clamp(18px, 5vw, 30px);
      box-shadow: 0 18px 55px #0005;
    }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .field { min-width: 0; margin-bottom: 17px; }
    .wide { grid-column: 1 / -1; }
    label, legend { display: block; margin-bottom: 8px; color: var(--text); font-size: 14px; font-weight: 600; }
    input, select {
      width: 100%; min-height: 48px; padding: 11px 12px;
      border: 1px solid var(--line); border-radius: 10px;
      background: #10100e; color: var(--text); font: inherit;
    }
    input:focus, select:focus, button:focus-visible, a:focus-visible {
      outline: 2px solid var(--gold); outline-offset: 2px;
    }
    .price { min-height: 22px; color: var(--gold); font-weight: 700; }
    .slots { display: flex; flex-wrap: wrap; gap: 9px; }
    .slot {
      min-width: 76px; min-height: 40px; padding: 8px 12px;
      border: 1px solid var(--line); border-radius: 9px;
      color: var(--text); background: #121210; font: inherit; cursor: pointer;
    }
    .slot:hover, .slot[aria-pressed="true"] { border-color: var(--gold); color: var(--gold); }
    .hint { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .submit, .whatsapp {
      display: inline-flex; align-items: center; justify-content: center;
      width: 100%; min-height: 50px; border: 0; border-radius: 10px;
      background: var(--gold); color: #17130b; font: inherit; font-weight: 750;
      text-decoration: none; cursor: pointer;
    }
    .submit:disabled { opacity: .48; cursor: not-allowed; }
    .message { min-height: 22px; margin: 0 0 12px; color: var(--error); font-size: 14px; }
    .success { text-align: center; }
    .success h2 { font-family: Georgia, serif; font-weight: 500; }
    .success .whatsapp { margin-top: 20px; background: #25d366; color: #062b15; }
    .privacy { margin-top: 18px; text-align: center; }
    footer { padding-top: 18px; color: var(--muted); text-align: center; font-size: 12px; }
    [hidden] { display: none !important; }
    @media (max-width: 520px) {
      main { padding-top: 24px; }
      .grid { grid-template-columns: 1fr; gap: 0; }
      .wide { grid-column: auto; }
      .card { border-radius: 14px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="mark" aria-hidden="true">BG</div>
      <p class="eyebrow">Barbearia Geslei</p>
      <h1>Seu próximo corte começa aqui.</h1>
      <p>Escolha o serviço, confira os horários disponíveis e solicite seu agendamento.</p>
    </header>

    <section class="card" aria-label="Agendamento">
      <form id="booking-form">
        <div class="grid">
          <div class="field">
            <label for="service">Serviço</label>
            <select id="service" name="service" required>
              <option value="">Selecione um serviço</option>
            </select>
            <p class="price" id="service-price" aria-live="polite"></p>
          </div>
          <div class="field">
            <label for="date">Data</label>
            <input id="date" name="date" type="date" required>
          </div>
          <div class="field wide">
            <label>Horários disponíveis</label>
            <div class="slots" id="slots" aria-live="polite">
              <p class="hint">Selecione o serviço e a data para consultar os horários.</p>
            </div>
          </div>
          <div class="field">
            <label for="name">Seu nome</label>
            <input id="name" name="name" autocomplete="name" maxlength="100" required>
          </div>
          <div class="field">
            <label for="phone">Seu WhatsApp</label>
            <input id="phone" name="phone" type="tel" autocomplete="tel" maxlength="25"
                   placeholder="(15) 99999-9999" required>
          </div>
        </div>
        <p class="message" id="message" role="status" aria-live="polite"></p>
        <button class="submit" id="submit" type="submit" disabled>Solicitar agendamento</button>
        <p class="hint privacy">Usaremos seus dados somente para organizar o horário e entrar em contato sobre o agendamento.</p>
      </form>

      <div class="success" id="success" hidden>
        <h2>Solicitação registrada!</h2>
        <p id="success-details"></p>
        <p class="hint">Toque abaixo para confirmar pelo WhatsApp da barbearia.</p>
        <a class="whatsapp" id="whatsapp-link" target="_blank" rel="noopener noreferrer">
          Confirmar pelo WhatsApp
        </a>
      </div>
    </section>
    <footer>Segunda a sexta: 18h às 00h · Sábado e domingo: 8h às 18h</footer>
  </main>
  <script>
    const CONFIG = __CONFIG__;
    const serviceField = document.querySelector("#service");
    const dateField = document.querySelector("#date");
    const slotsBox = document.querySelector("#slots");
    const message = document.querySelector("#message");
    const submitButton = document.querySelector("#submit");
    const form = document.querySelector("#booking-form");
    let selectedSlot = "";

    function localDateString() {
      const local = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
      return local.toISOString().slice(0, 10);
    }

    function showMessage(text) {
      message.textContent = text;
    }

    function clearSlots(text) {
      slotsBox.replaceChildren();
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = text;
      slotsBox.append(hint);
      selectedSlot = "";
      submitButton.disabled = true;
    }

    function updateServicePrice() {
      const service = CONFIG.services[serviceField.value];
      document.querySelector("#service-price").textContent = service
        ? `R$ ${service.price.toFixed(2).replace(".", ",")} · ${service.duration} min`
        : "";
    }

    async function loadSlots() {
      showMessage("");
      updateServicePrice();

      if (!serviceField.value || !dateField.value) {
        clearSlots("Selecione o serviço e a data para consultar os horários.");
        return;
      }

      clearSlots("Buscando horários...");

      try {
        const query = new URLSearchParams({
          date: dateField.value,
          service: serviceField.value
        });
        const response = await fetch(`/api/slots?${query}`);
        const result = await response.json();

        if (!response.ok) {
          throw new Error(result.error || "Não foi possível consultar os horários.");
        }

        slotsBox.replaceChildren();

        if (!result.slots.length) {
          clearSlots("Não há horários disponíveis nessa data. Escolha outro dia.");
          return;
        }

        for (const slot of result.slots) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "slot";
          button.textContent = slot;
          button.setAttribute("aria-pressed", "false");

          button.addEventListener("click", () => {
            selectedSlot = slot;

            for (const item of slotsBox.querySelectorAll(".slot")) {
              item.setAttribute("aria-pressed", String(item === button));
            }

            submitButton.disabled = false;
          });

          slotsBox.append(button);
        }
      } catch (error) {
        clearSlots(error.message);
      }
    }

    for (const [name, service] of Object.entries(CONFIG.services)) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      serviceField.append(option);
    }

    dateField.min = localDateString();
    dateField.value = localDateString();
    serviceField.addEventListener("change", loadSlots);
    dateField.addEventListener("change", loadSlots);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      showMessage("");

      if (!selectedSlot) {
        showMessage("Escolha um horário disponível.");
        return;
      }

      submitButton.disabled = true;
      submitButton.textContent = "Registrando...";

      try {
        const response = await fetch("/api/bookings", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            name: document.querySelector("#name").value,
            phone: document.querySelector("#phone").value,
            service: serviceField.value,
            date: dateField.value,
            time: selectedSlot
          })
        });

        const result = await response.json();

        if (!response.ok) {
          throw new Error(result.error || "Não foi possível registrar o horário.");
        }

        form.hidden = true;

        const details =
          `${result.service} em ${result.date} às ${result.time} · ${result.price}`;
        document.querySelector("#success-details").textContent = details;

        const text =
          `Olá! Acabei de solicitar um horário na Barbearia Geslei: ${result.service}, ${result.date} às ${result.time}.`;
        const whatsapp = document.querySelector("#whatsapp-link");
        whatsapp.href =
          `https://wa.me/${CONFIG.whatsapp}?text=${encodeURIComponent(text)}`;

        document.querySelector("#success").hidden = false;
      } catch (error) {
        await loadSlots();
        showMessage(error.message);
      } finally {
        submitButton.textContent = "Solicitar agendamento";
      }
    });

    loadSlots();
  </script>
</body>
</html>
"""


def build_page():
    config = {
        "services": SERVICES,
        "whatsapp": BUSINESS_WHATSAPP,
    }
    config_json = json.dumps(config, ensure_ascii=False).replace("<", "\\u003c")
    return PAGE_TEMPLATE.replace("__CONFIG__", config_json).encode("utf-8")


def ensure_database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    try:
        cursor = connection.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            ultimo_corte TEXT
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agendamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            hora TEXT NOT NULL,
            cliente TEXT NOT NULL,
            servico TEXT NOT NULL,
            valor REAL NOT NULL DEFAULT 0,
            pago INTEGER NOT NULL DEFAULT 0,
            duracao INTEGER NOT NULL DEFAULT 0
        )
        """)
        cursor.execute("PRAGMA table_info(agendamentos)")
        columns = {row[1] for row in cursor.fetchall()}

        if "valor" not in columns:
            cursor.execute("""
            ALTER TABLE agendamentos
            ADD COLUMN valor REAL NOT NULL DEFAULT 0
            """)
            cursor.execute("""
            UPDATE agendamentos
            SET valor = CASE servico
                WHEN 'Cabelo' THEN 25.0
                WHEN 'Barba' THEN 25.0
                WHEN 'Sobrancelha' THEN 10.0
                WHEN 'Combo' THEN 60.0
                ELSE 0
            END
            """)

        if "pago" not in columns:
            cursor.execute("""
            ALTER TABLE agendamentos
            ADD COLUMN pago INTEGER NOT NULL DEFAULT 0
            """)

        if "duracao" not in columns:
            cursor.execute("""
            ALTER TABLE agendamentos
            ADD COLUMN duracao INTEGER NOT NULL DEFAULT 0
            """)

        cursor.execute("""
        UPDATE agendamentos
        SET duracao = CASE servico
            WHEN 'Cabelo' THEN 30
            WHEN 'Barba' THEN 20
            WHEN 'Sobrancelha' THEN 15
            WHEN 'Combo' THEN 50
            ELSE 30
        END
        WHERE duracao IS NULL OR duracao <= 0
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agendamentos_data
        ON agendamentos(data)
        """)
        connection.commit()

    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_date(value):
    if not isinstance(value, str):
        return None

    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass

    return None


def business_hours(day):
    if day.weekday() < 5:
        return 18 * 60, 24 * 60

    return 8 * 60, 18 * 60


def booked_intervals(cursor, selected_date):
    cursor.execute("""
    SELECT data, hora, servico, duracao
    FROM agendamentos
    WHERE data IN (?, ?, ?)
    """, (
        selected_date.isoformat(),
        selected_date.strftime("%d/%m/%Y"),
        selected_date.strftime("%d-%m-%Y"),
    ))

    intervals = []

    for stored_date, stored_time, service, duration in cursor.fetchall():
        if not isinstance(stored_date, str) or parse_date(stored_date) != selected_date:
            continue

        try:
            stored_time_value = datetime.strptime(stored_time, "%H:%M")
        except (TypeError, ValueError):
            continue

        start = stored_time_value.hour * 60 + stored_time_value.minute

        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = 0

        if duration <= 0:
            duration = SERVICES.get(service, {"duration": 30})["duration"]

        intervals.append((start, start + duration))

    return intervals


def available_slots(cursor, selected_date, service_name):
    if selected_date < date.today():
        return []

    opening, closing = business_hours(selected_date)
    duration = SERVICES[service_name]["duration"]
    intervals = booked_intervals(cursor, selected_date)
    slots = []
    current_time = datetime.now()

    start = opening

    while start + duration <= closing:
        end = start + duration

        is_past = (
            selected_date == current_time.date()
            and start <= current_time.hour * 60 + current_time.minute
        )

        has_conflict = any(
            start < booked_end and booked_start < end
            for booked_start, booked_end in intervals
        )

        if not is_past and not has_conflict:
            slots.append("{:02d}:{:02d}".format(start // 60, start % 60))

        start += SLOT_INTERVAL_MINUTES

    return slots


def create_booking(payload):
    if not isinstance(payload, dict):
        raise InvalidBooking("Dados do agendamento inválidos.")

    name = str(payload.get("name", "")).strip()
    phone = re.sub(r"\D", "", str(payload.get("phone", "")))
    service_name = payload.get("service")
    date_text = payload.get("date")
    time_text = payload.get("time")

    if not name or len(name) > 100:
        raise InvalidBooking("Informe seu nome (até 100 caracteres).")
    if not 10 <= len(phone) <= 15:
        raise InvalidBooking("Informe um telefone com DDD e até 15 dígitos.")
    if service_name not in SERVICES:
        raise InvalidBooking("Selecione um serviço válido.")
    if not isinstance(date_text, str) or not isinstance(time_text, str):
        raise InvalidBooking("Informe uma data e um horário válidos.")

    selected_date = parse_date(date_text)
    if selected_date is None or selected_date < date.today():
        raise InvalidBooking("Escolha uma data de hoje em diante.")
    if not re.fullmatch(r"\d{2}:\d{2}", time_text):
        raise InvalidBooking("Escolha um horário disponível.")

    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    try:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        slots = available_slots(cursor, selected_date, service_name)
        if time_text not in slots:
            raise SlotUnavailable(
                "Esse horário acabou de ficar indisponível. Escolha outro."
            )

        cursor.execute(
            "SELECT id FROM clientes WHERE telefone = ? LIMIT 1",
            (phone,)
        )
        existing_client = cursor.fetchone()

        if existing_client:
            cursor.execute(
                "UPDATE clientes SET nome = ? WHERE id = ?",
                (name, existing_client[0])
            )
        else:
            cursor.execute("""
            INSERT INTO clientes (nome, telefone, ultimo_corte)
            VALUES (?, ?, '')
            """, (name, phone))

        service = SERVICES[service_name]
        cursor.execute("""
        INSERT INTO agendamentos
        (data, hora, cliente, servico, valor, pago, duracao)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (
            selected_date.isoformat(),
            time_text,
            name,
            service_name,
            service["price"],
            service["duration"],
        ))
        connection.commit()

    except (InvalidBooking, sqlite3.Error):
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "service": service_name,
        "date": selected_date.strftime("%d/%m/%Y"),
        "time": time_text,
        "price": "R$ {:.2f}".format(service["price"]).replace(".", ","),
    }


class BookingHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def require_admin(self):
        expected_token = os.environ.get("BARBEARIA_ADMIN_TOKEN", "")
        authorization = self.headers.get("Authorization", "")

        if not expected_token:
            self.send_json(
                503,
                {
                    "error":
                    "Acesso administrativo não foi configurado no servidor."
                }
            )
            return False

        if not hmac.compare_digest(
            authorization,
            "Bearer " + expected_token
        ):
            self.send_json(401, {"error": "Acesso não autorizado."})
            return False

        return True

    def do_GET(self):
        request = urlsplit(self.path)

        if request.path == "/":
            body = build_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if request.path == "/api/admin/clients":
            if not self.require_admin():
                return

            connection = sqlite3.connect(DATABASE_PATH, timeout=10)
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT nome FROM clientes ORDER BY nome")
                self.send_json(200, {
                    "clients": [row[0] for row in cursor.fetchall()]
                })
            except sqlite3.Error:
                LOGGER.exception("Falha ao consultar clientes para o desktop")
                self.send_json(
                    500,
                    {"error": "Não foi possível consultar os clientes."}
                )
            finally:
                connection.close()

            return

        if request.path == "/api/admin/appointments":
            if not self.require_admin():
                return

            connection = sqlite3.connect(DATABASE_PATH, timeout=10)
            try:
                cursor = connection.cursor()
                query = """
                SELECT id, data, hora, cliente, servico, valor, pago
                FROM agendamentos
                """

                date_text = parse_qs(request.query).get("date", [""])[0]
                selected_date = parse_date(date_text) if date_text else None

                if date_text and selected_date is None:
                    self.send_json(400, {"error": "Data inválida."})
                    return

                if selected_date:
                    cursor.execute(
                        query + """
                        WHERE data IN (?, ?, ?)
                        ORDER BY data, hora
                        """,
                        (
                            selected_date.isoformat(),
                            selected_date.strftime("%d/%m/%Y"),
                            selected_date.strftime("%d-%m-%Y"),
                        )
                    )
                else:
                    cursor.execute(query + " ORDER BY data, hora")

                appointments = [
                    {
                        "id": row[0],
                        "date": row[1],
                        "time": row[2],
                        "client": row[3],
                        "service": row[4],
                        "price": row[5],
                        "paid": bool(row[6]),
                    }
                    for row in cursor.fetchall()
                ]

                self.send_json(200, {"appointments": appointments})

            except sqlite3.Error:
                LOGGER.exception(
                    "Falha ao consultar agendamentos para o desktop"
                )
                self.send_json(
                    500,
                    {"error": "Não foi possível consultar a agenda."}
                )
            finally:
                connection.close()

            return

        if request.path != "/api/slots":
            self.send_json(404, {"error": "Página não encontrada."})
            return

        query = parse_qs(request.query)
        date_text = query.get("date", [""])[0]
        service_name = query.get("service", [""])[0]
        selected_date = parse_date(date_text)

        if selected_date is None or service_name not in SERVICES:
            self.send_json(
                400,
                {"error": "Selecione uma data e um serviço válidos."}
            )
            return

        connection = sqlite3.connect(DATABASE_PATH, timeout=10)
        try:
            cursor = connection.cursor()
            self.send_json(200, {
                "slots": available_slots(cursor, selected_date, service_name)
            })
        except sqlite3.Error:
            LOGGER.exception("Falha ao consultar horários")
            self.send_json(
                500,
                {"error": "Não foi possível consultar os horários agora."}
            )
        finally:
            connection.close()

    def do_POST(self):
        request_path = urlsplit(self.path).path

        payment_match = re.fullmatch(
            r"/api/admin/appointments/(\d+)/payment",
            request_path
        )

        allowed_paths = {
            "/api/bookings",
            "/api/admin/appointments",
        }

        if request_path not in allowed_paths and not payment_match:
            self.send_json(404, {"error": "Página não encontrada."})
            return

        if payment_match:
            if not self.require_admin():
                return

            appointment_id = int(payment_match.group(1))
            connection = sqlite3.connect(DATABASE_PATH, timeout=10)

            try:
                cursor = connection.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    "SELECT pago FROM agendamentos WHERE id = ?",
                    (appointment_id,)
                )
                record = cursor.fetchone()

                if record is None:
                    connection.rollback()
                    self.send_json(
                        404,
                        {"error": "Agendamento não encontrado."}
                    )
                    return

                new_status = 0 if record[0] else 1
                cursor.execute(
                    "UPDATE agendamentos SET pago = ? WHERE id = ?",
                    (new_status, appointment_id)
                )
                connection.commit()
                self.send_json(200, {"paid": bool(new_status)})

            except sqlite3.Error:
                connection.rollback()
                LOGGER.exception(
                    "Falha ao atualizar pagamento do agendamento"
                )
                self.send_json(
                    500,
                    {"error": "Não foi possível atualizar o pagamento."}
                )
            finally:
                connection.close()

            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Solicitação inválida."})
            return

        if content_length <= 0 or content_length > 4096:
            self.send_json(
                413,
                {"error": "Solicitação inválida ou muito grande."}
            )
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(
                400,
                {"error": "Os dados enviados não são válidos."}
            )
            return

        if request_path == "/api/admin/appointments":
            if not self.require_admin():
                return

            try:
                appointment_id = create_admin_appointment(payload)
            except SlotUnavailable as error:
                self.send_json(409, {"error": str(error)})
            except InvalidBooking as error:
                self.send_json(400, {"error": str(error)})
            except sqlite3.Error:
                LOGGER.exception("Falha ao salvar agendamento do desktop")
                self.send_json(
                    500,
                    {"error": "Não foi possível salvar o horário."}
                )
            else:
                self.send_json(201, {"id": appointment_id})

            return

        try:
            booking = create_booking(payload)
        except SlotUnavailable as error:
            self.send_json(409, {"error": str(error)})
        except InvalidBooking as error:
            self.send_json(400, {"error": str(error)})
        except sqlite3.Error:
            LOGGER.exception("Falha ao salvar agendamento")
            self.send_json(
                500,
                {"error": "Não foi possível salvar o horário agora."}
            )
        else:
            self.send_json(201, booking)

    def log_message(self, format_string, *args):
        LOGGER.info(
            "%s - %s",
            self.address_string(),
            format_string % args
        )


def main():
    parser = argparse.ArgumentParser(
        description="Agenda online local da Barbearia Geslei."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Endereço de escuta."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8765")),
        help="Porta HTTP."
    )
    args = parser.parse_args()

    ensure_database()
    server = ThreadingHTTPServer((args.host, args.port), BookingHandler)
    LOGGER.info("Agenda iniciada em %s:%s", args.host, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Encerrando agenda.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()