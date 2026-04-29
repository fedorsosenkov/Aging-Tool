"""
Генерация читаемого HTML-отчёта по результатам анализа старения.
"""

from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import Optional

from core.aging import AgingResults


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Отчёт об анализе старения транзисторов</title>
<style>
  :root {{
    --bg: #f8f9fa;
    --card: #ffffff;
    --accent: #2563eb;
    --accent-light: #eff6ff;
    --border: #e2e8f0;
    --text: #1e293b;
    --muted: #64748b;
    --good: #16a34a;
    --warn: #d97706;
    --bad: #dc2626;
    --nmos: #1d4ed8;
    --pmos: #9333ea;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 32px 16px;
    line-height: 1.6;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.75rem; color: var(--accent); margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 32px; font-size: 0.95rem; }}
  .meta-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .meta-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
  }}
  .meta-card .label {{ font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }}
  .meta-card .value {{ font-size: 1.2rem; font-weight: 600; color: var(--accent); margin-top: 4px; }}
  h2 {{
    font-size: 1.1rem;
    margin: 32px 0 12px;
    padding-bottom: 6px;
    border-bottom: 2px solid var(--border);
  }}
  h2 .badge {{
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 99px;
    margin-left: 8px;
    vertical-align: middle;
  }}
  .nmos-badge {{ background: #dbeafe; color: var(--nmos); }}
  .pmos-badge {{ background: #f3e8ff; color: var(--pmos); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
    margin-bottom: 24px;
  }}
  th {{
    background: var(--accent-light);
    color: var(--accent);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: .04em;
    padding: 10px 16px;
    text-align: left;
    font-weight: 600;
  }}
  td {{
    padding: 10px 16px;
    border-top: 1px solid var(--border);
    font-size: 0.9rem;
  }}
  tr:hover td {{ background: var(--bg); }}
  .chip {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: 0.78rem;
    font-weight: 600;
  }}
  .chip-good {{ background: #dcfce7; color: var(--good); }}
  .chip-warn {{ background: #fef3c7; color: var(--warn); }}
  .chip-bad  {{ background: #fee2e2; color: var(--bad);  }}
  .mono {{ font-family: 'Cascadia Code', 'Consolas', monospace; }}
  footer {{
    margin-top: 48px;
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>Анализ старения МОП-транзисторов</h1>
  <p class="subtitle">Сформирован {date} · Время наработки: {years} лет</p>

  <div class="meta-grid">
    <div class="meta-card">
      <div class="label">Температура</div>
      <div class="value">{temp_c} °C / {temp_k} K</div>
    </div>
    <div class="meta-card">
      <div class="label">Напряжение питания</div>
      <div class="value">{supply_voltage}</div>
    </div>
    <div class="meta-card">
      <div class="label">Время симуляции</div>
      <div class="value">{sim_time}</div>
    </div>
    <div class="meta-card">
      <div class="label">NMOS (HCI)</div>
      <div class="value">{n_nmos}</div>
    </div>
    <div class="meta-card">
      <div class="label">PMOS (NBTI)</div>
      <div class="value">{n_pmos}</div>
    </div>
  </div>

  {hci_section}
  {nbti_section}

  <footer>Отчёт сгенерирован программой анализа деградации МОП-транзисторов</footer>
</div>
</body>
</html>
"""


def _severity_chip(delta_vth: float) -> str:
    if delta_vth < 0.005:
        return '<span class="chip chip-good">Низкая</span>'
    elif delta_vth < 0.02:
        return '<span class="chip chip-warn">Средняя</span>'
    else:
        return '<span class="chip chip-bad">Высокая</span>'


def _fmt_time(seconds: float) -> str:
    if seconds == 0:
        return "—"
    if seconds < 1e-12:
        return f"{seconds * 1e15:.3g} фс"
    if seconds < 1e-9:
        return f"{seconds * 1e12:.3g} пс"
    if seconds < 1e-6:
        return f"{seconds * 1e9:.3g} нс"
    if seconds < 1e-3:
        return f"{seconds * 1e6:.3g} мкс"
    return f"{seconds:.3g} с"


def _fmt_voltage(v: float) -> str:
    return f"{v * 1000:.3f} мВ" if abs(v) < 0.1 else f"{v:.5f} В"


def _fmt_current(a: float) -> str:
    """Форматирует ток: А → нА / мкА / мА / А."""
    aa = abs(a)
    if aa == 0:
        return "0 А"
    if aa < 1e-9:
        return f"{a * 1e12:.3g} пА"
    if aa < 1e-6:
        return f"{a * 1e9:.3g} нА"
    if aa < 1e-3:
        return f"{a * 1e6:.3g} мкА"
    if aa < 1:
        return f"{a * 1e3:.3g} мА"
    return f"{a:.3g} А"


def _fmt_charge(c: float) -> str:
    """Форматирует заряд А·с (Кл) → пКл / нКл / мкКл."""
    ac = abs(c)
    if ac == 0:
        return "0 Кл"
    if ac < 1e-9:
        return f"{c * 1e12:.3g} пКл"
    if ac < 1e-6:
        return f"{c * 1e9:.3g} нКл"
    if ac < 1e-3:
        return f"{c * 1e6:.3g} мкКл"
    return f"{c:.3g} Кл"


def _build_hci_section(results: AgingResults) -> str:
    nmos = results.nmos_results()
    if not nmos:
        return ""
    sorted_nmos = sorted(nmos, key=lambda x: x.hci_delta_vth_v, reverse=True)
    rows = []
    for ar in sorted_nmos:
        rows.append(f"""
    <tr>
      <td class="mono"><b>{ar.name}</b></td>
      <td>{_fmt_time(ar.hci_active_time_s)}</td>
      <td>{ar.hci_ratio:.4e}</td>
      <td class="mono">{_fmt_voltage(ar.hci_delta_vth_v)}</td>
      <td class="mono">{ar.hci_mobility_factor:.6f}</td>
      <td>{_severity_chip(ar.hci_delta_vth_v)}</td>
    </tr>""")
    raw_rows = []
    for ar in sorted_nmos:
        raw_rows.append(f"""
    <tr>
      <td class="mono"><b>{ar.name}</b></td>
      <td class="mono">{_fmt_charge(ar.hci_integ_a_s)}</td>
      <td class="mono">{_fmt_current(ar.hci_max_hci_a)}</td>
      <td class="mono">{_fmt_current(ar.hci_max_id_a)}</td>
    </tr>""")
    return f"""
  <h2>Эффект горячих носителей (HCI) <span class="badge nmos-badge">NMOS</span></h2>
  <table>
    <thead>
      <tr>
        <th>Транзистор</th>
        <th>Время в режиме HCI</th>
        <th>Отношение времени работы в режиме HCI ко времени моделирования</th>
        <th>ΔVth</th>
        <th>Коэф. подвижности (÷u0)</th>
        <th>Степень деградации</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}
    </tbody>
  </table>
  <details style="margin-bottom:24px">
    <summary style="cursor:pointer;color:var(--muted);font-size:0.85rem;padding:4px 0">
      Данные из LTspice (исходные измерения)
    </summary>
    <table style="margin-top:8px">
      <thead>
        <tr>
          <th>Транзистор</th>
          <th>∫ I_hci dt</th>
          <th>max I_hci</th>
          <th>max |Id|</th>
        </tr>
      </thead>
      <tbody>{''.join(raw_rows)}
      </tbody>
    </table>
  </details>"""


def _build_nbti_section(results: AgingResults) -> str:
    pmos = results.pmos_results()
    if not pmos:
        return ""
    sorted_pmos = sorted(pmos, key=lambda x: x.nbti_delta_vth_v, reverse=True)
    rows = []
    for ar in sorted_pmos:
        rows.append(f"""
    <tr>
      <td class="mono"><b>{ar.name}</b></td>
      <td>{_fmt_time(ar.nbti_active_time_s)}</td>
      <td>{ar.nbti_ratio:.4f}</td>
      <td class="mono">{_fmt_voltage(ar.nbti_delta_vth_v)}</td>
      <td class="mono">{ar.nbti_mobility_factor:.6f}</td>
      <td>{_severity_chip(ar.nbti_delta_vth_v)}</td>
    </tr>""")
    raw_rows = []
    for ar in sorted_pmos:
        raw_rows.append(f"""
    <tr>
      <td class="mono"><b>{ar.name}</b></td>
      <td class="mono">{ar.nbti_integ_v_s:.4e} В·с</td>
      <td class="mono">{ar.nbti_max_vsg_vdg:.4e} В²</td>
    </tr>""")
    return f"""
  <h2>Температурная нестабильность при отрицательном смещении (NBTI) <span class="badge pmos-badge">PMOS</span></h2>
  <table>
    <thead>
      <tr>
        <th>Транзистор</th>
        <th>Время в режиме NBTI</th>
        <th>Отношение времени работы в режиме NBTI ко времени моделирования</th>
        <th>ΔVth</th>
        <th>Коэф. подвижности (÷u0)</th>
        <th>Степень деградации</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}
    </tbody>
  </table>
  <details style="margin-bottom:24px">
    <summary style="cursor:pointer;color:var(--muted);font-size:0.85rem;padding:4px 0">
      Данные из LTspice (исходные измерения)
    </summary>
    <table style="margin-top:8px">
      <thead>
        <tr>
          <th>Транзистор</th>
          <th>∫ |Vsg| dt</th>
          <th>max(Vsg · Vdg)</th>
        </tr>
      </thead>
      <tbody>{''.join(raw_rows)}
      </tbody>
    </table>
  </details>"""


def _fmt_sim_time(s: float) -> str:
    if s < 1e-9:
        return f"{s * 1e12:.3g} пс"
    if s < 1e-6:
        return f"{s * 1e9:.3g} нс"
    return f"{s:.3g} с"


_TEMP_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Температурный анализ деградации МОП-транзисторов</title>
<style>
  :root {{
    --bg: #f8f9fa;
    --card: #ffffff;
    --accent: #0369a1;
    --accent-light: #e0f2fe;
    --border: #e2e8f0;
    --text: #1e293b;
    --muted: #64748b;
    --good: #16a34a;
    --warn: #d97706;
    --bad: #dc2626;
    --nmos: #1d4ed8;
    --pmos: #9333ea;
    --fresh: #475569;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 32px 16px;
    line-height: 1.6;
  }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.75rem; color: var(--accent); margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 32px; font-size: 0.95rem; }}
  .meta-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .meta-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
  }}
  .meta-card .label {{ font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }}
  .meta-card .value {{ font-size: 1.2rem; font-weight: 600; color: var(--accent); margin-top: 4px; }}
  h2 {{
    font-size: 1.1rem;
    margin: 32px 0 12px;
    padding-bottom: 6px;
    border-bottom: 2px solid var(--border);
  }}
  h2 .badge {{
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 99px;
    margin-left: 8px;
    vertical-align: middle;
  }}
  .nmos-badge {{ background: #dbeafe; color: var(--nmos); }}
  .pmos-badge {{ background: #f3e8ff; color: var(--pmos); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
    margin-bottom: 24px;
    font-size: 0.88rem;
  }}
  th {{
    background: var(--accent-light);
    color: var(--accent);
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: .04em;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
  }}
  th.col-fresh {{ color: var(--fresh); background: #f1f5f9; }}
  td {{
    padding: 9px 12px;
    border-top: 1px solid var(--border);
  }}
  td.col-fresh {{ color: var(--fresh); }}
  tr:hover td {{ background: var(--bg); }}
  .chip {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: 0.78rem;
    font-weight: 600;
  }}
  .chip-good {{ background: #dcfce7; color: var(--good); }}
  .chip-warn {{ background: #fef3c7; color: var(--warn); }}
  .chip-bad  {{ background: #fee2e2; color: var(--bad);  }}
  .mono {{ font-family: 'Cascadia Code', 'Consolas', monospace; }}
  .legend {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
    margin-top: 8px;
    font-size: 0.85rem;
    color: var(--muted);
  }}
  .legend dt {{ font-weight: 600; color: var(--text); }}
  .legend dd {{ margin-left: 16px; margin-bottom: 6px; }}
  footer {{
    margin-top: 48px;
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>Температурный анализ деградации МОП-транзисторов</h1>
  <p class="subtitle">Сформирован {date} · Время наработки: {years} лет</p>

  <div class="meta-grid">
    <div class="meta-card">
      <div class="label">Базовая температура (T₀)</div>
      <div class="value">{t0_c} °C</div>
    </div>
    <div class="meta-card">
      <div class="label">Новая температура (T_new)</div>
      <div class="value">{t_new_c} °C</div>
    </div>
    <div class="meta-card">
      <div class="label">NMOS (HCI)</div>
      <div class="value">{n_nmos}</div>
    </div>
    <div class="meta-card">
      <div class="label">PMOS (NBTI)</div>
      <div class="value">{n_pmos}</div>
    </div>
  </div>

  {hci_section}
  {nbti_section}

  <div class="legend">
    <dl>
      <dt>Свежий</dt>
      <dd>Новый транзистор без деградации: ΔVth = 0 мВ, μ = 1.000000</dd>
      <dt>T₀ / T_new</dt>
      <dd>Деградация (ΔVth, μ) после {years} лет при соответствующей температуре</dd>
      <dt>Δ(T_new − T₀)</dt>
      <dd>Изменение параметров деградации при переходе к новой температуре</dd>
      <dt>μ (коэф. деградации подвижности)</dt>
      <dd>u0_aged = u0 / μ. Чем больше μ, тем сильнее деградация подвижности носителей</dd>
    </dl>
  </div>

  <footer>Отчёт сгенерирован программой анализа деградации МОП-транзисторов</footer>
</div>
</body>
</html>
"""


def _delta_color(mv: float) -> str:
    """CSS-цвет для колонки Δ(T_new − T₀): зелёный / оранжевый / красный."""
    if abs(mv) < 5:
        return "#16a34a"   # зелёный — незначительное изменение
    elif abs(mv) < 20:
        return "#d97706"   # оранжевый — умеренное
    return "#dc2626"       # красный — значительное


def _build_temp_section(comparisons: list, channel: str, label: str, badge_class: str) -> str:
    """Строит HTML-секцию с таблицей для заданного типа канала (NMOS или PMOS)."""
    subset = sorted(
        [c for c in comparisons if c.channel == channel],
        key=lambda c: c.delta_vth_tnew_v,
        reverse=True,
    )
    if not subset:
        return ""

    rows = []
    for c in subset:
        delta_mv = c.delta_vth_change_mv
        delta_mob = c.mobility_change_pct
        d_color  = _delta_color(delta_mv)
        dm_color = _delta_color(delta_mob)
        rows.append(f"""
    <tr>
      <td class="mono"><b>{c.name}</b></td>
      <td class="mono col-fresh">0.000 мВ</td>
      <td class="mono">{_fmt_voltage(c.delta_vth_t0_v)}</td>
      <td class="mono">{_fmt_voltage(c.delta_vth_tnew_v)}</td>
      <td class="mono" style="color:{d_color};font-weight:600">{delta_mv:+.3f} мВ</td>
      <td class="mono col-fresh">1.000000</td>
      <td class="mono">{c.mobility_factor_t0:.6f}</td>
      <td class="mono">{c.mobility_factor_tnew:.6f}</td>
      <td class="mono" style="color:{dm_color};font-weight:600">{delta_mob:+.3f} %</td>
      <td>{_severity_chip(c.delta_vth_tnew_v)}</td>
    </tr>""")

    return f"""
  <h2>{label} <span class="badge {badge_class}">{channel}</span></h2>
  <table>
    <thead>
      <tr>
        <th>Транзистор</th>
        <th class="col-fresh">Свежий ΔVth</th>
        <th>ΔVth (T₀)</th>
        <th>ΔVth (T_new)</th>
        <th>Δ(T_new − T₀)</th>
        <th class="col-fresh">Свежий μ</th>
        <th>μ (T₀)</th>
        <th>μ (T_new)</th>
        <th>Δμ</th>
        <th>Степень</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}
    </tbody>
  </table>"""


def generate_temp_html_report(
    comparisons: list,
    target_years: float,
    output_path: str | Path,
) -> Path:
    """
    Создаёт HTML-отчёт по результатам температурного анализа.

    Таблица: Свежий → T₀ → T_new (9 колонок + «Степень деградации»).

    Parameters
    ----------
    comparisons  : list[TemperatureComparisonResult]
    target_years : время наработки (лет)
    output_path  : полный путь к выходному HTML-файлу

    Returns
    -------
    Path к созданному файлу.
    """
    output_path = Path(output_path)

    t0   = comparisons[0].t0_c   if comparisons else 27.0
    tnew = comparisons[0].t_new_c if comparisons else 27.0

    n_nmos = sum(1 for c in comparisons if c.channel == "NMOS")
    n_pmos = sum(1 for c in comparisons if c.channel == "PMOS")

    hci_sec  = _build_temp_section(
        comparisons, "NMOS",
        "Эффект горячих носителей (HCI)",
        "nmos-badge",
    )
    nbti_sec = _build_temp_section(
        comparisons, "PMOS",
        "Температурная нестабильность при отрицательном смещении (NBTI)",
        "pmos-badge",
    )

    html = _TEMP_HTML_TEMPLATE.format(
        date=datetime.now().strftime("%d.%m.%Y %H:%M"),
        years=int(target_years),
        t0_c=f"{t0:.1f}",
        t_new_c=f"{tnew:.1f}",
        n_nmos=n_nmos,
        n_pmos=n_pmos,
        hci_section=hci_sec,
        nbti_section=nbti_sec,
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path


def generate_html_report(
    results: AgingResults,
    target_years: float,
    output_path: str | Path,
    supply_voltage_v: Optional[float] = None,
) -> Path:
    """Записывает HTML-отчёт и возвращает путь к файлу."""
    output_path = Path(output_path)

    supply_str = f"{supply_voltage_v:g} В" if supply_voltage_v is not None else "—"

    html = _HTML_TEMPLATE.format(
        date=datetime.now().strftime("%d.%m.%Y %H:%M"),
        years=int(target_years),
        temp_c=int(results.temperature_c),
        temp_k=int(results.temperature_k),
        supply_voltage=supply_str,
        sim_time=_fmt_sim_time(results.sim_time_s),
        n_nmos=len(results.nmos_results()),
        n_pmos=len(results.pmos_results()),
        hci_section=_build_hci_section(results),
        nbti_section=_build_nbti_section(results),
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path
