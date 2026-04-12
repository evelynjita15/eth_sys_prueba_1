import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
import base64
import json
import re

# =================================================================
# 1. CONFIGURACIÓN DE LA PÁGINA Y ESTILOS (Sin colores gris/negro)
# =================================================================
st.set_page_config(page_title="Simulador de Procesos y TEA", layout="wide")

st.markdown("""
    <style>
    div[data-testid="metric-container"] { 
        background-color: #0F172A !important; /* Azul marino profundo */
        padding: 15px !important; 
        border-radius: 10px !important; 
        border: 1px solid #1E3A8A !important; /* Borde azul brillante */
    }
    div[data-testid="metric-container"] > div, 
    div[data-testid="metric-container"] label {
        color: #F8FAFC !important;
    }
    </style>
    """, unsafe_allow_html=True)

# Inicialización de variables de sesión para control bidireccional (Punto 16)
if 't_entrada' not in st.session_state: st.session_state.t_entrada = 25
if 't_w220' not in st.session_state: st.session_state.t_w220 = 92
if 'p_sep' not in st.session_state: st.session_state.p_sep = 101325
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'sim_history' not in st.session_state: st.session_state.sim_history = pd.DataFrame(columns=["Iteración", "Temp_Mosto", "Temp_W220", "Presion_V100", "NPV", "Costo_Prod"])
if 'iteracion' not in st.session_state: st.session_state.iteracion = 1

def manejar_pdf(ruta_archivo):
    try:
        with open(ruta_archivo, "rb") as f:
            st.download_button(
                label=f"📥 Descargar y Ver Plano ISO ({ruta_archivo})",
                data=f,
                file_name=ruta_archivo,
                mime="application/pdf",
                use_container_width=True
            )
    except FileNotFoundError:
        st.error(f"⚠️ No se encontró '{ruta_archivo}'. Asegúrate de que el archivo esté subido en la misma carpeta que app.py en GitHub.")

# =================================================================
# 2. CLASE DE INGENIERÍA ECONÓMICA (TEA)
# =================================================================
class TEA_Didactico(bst.TEA):
    def _DPI(self, installed_equipment_cost): return self.purchase_cost
    def _TDC(self, DPI): return DPI
    def _FCI(self, TDC): return self.purchase_cost * self.lang_factor
    def _TCI(self, FCI): return FCI + self.WC
    def _FOC(self, FCI): return 0.0
    @property
    def VOC(self):
        mat = getattr(self.system, "material_cost", 0)
        util = getattr(self.system, "utility_cost", 0)
        return mat + util

# =================================================================
# 3. LÓGICA DE SIMULACIÓN Y COSTOS
# =================================================================
def run_simulation(w_flow, e_flow, t_in, t_w220, p_flash, p_luz, p_vapor, p_agua, p_mosto, p_etanol):
    bst.main_flowsheet.clear()
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    mosto = bst.Stream("MOSTO", Water=w_flow, Ethanol=e_flow, units="kg/hr", T=t_in + 273.15, P=101325)
    mosto.price = p_mosto
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15, P=300000)

    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("Mosto_Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=t_w220 + 273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=p_flash)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Vinazas"), P=p_flash, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    bst.PowerUtility.price = p_luz
    vapor = bst.HeatUtility.get_agent("low_pressure_steam")
    vapor.heat_transfer_price = p_vapor
    agua = bst.HeatUtility.get_agent("cooling_water")
    agua.heat_transfer_price = p_agua

    sys = bst.System("sys_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()

    producto = W310.outs[0]
    producto.price = p_etanol 

    tea = TEA_Didactico(
        system=sys, IRR=0.15, duration=(2025, 2045), income_tax=0.3,
        depreciation="MACRS7", construction_schedule=(0.4, 0.6),
        startup_months=6, startup_FOCfrac=0.5, startup_VOCfrac=0.5,
        startup_salesfrac=0.5, operating_days=330, lang_factor=4.0,
        WC_over_FCI=0.05, finance_interest=0.0, finance_years=0.0,
        finance_fraction=0.0
    )

    npv_actual = tea.NPV
    roi_actual = tea.ROI * 100 
    pbp_actual = tea.PBP
    tea.IRR = 0.0
    costo_prod = tea.solve_price(producto)
    tea.IRR = 0.15
    precio_sug = tea.solve_price(producto)

    return sys, W310, npv_actual, roi_actual, pbp_actual, costo_prod, precio_sug

def generar_tablas(sistema):
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0.1:
            datos_mat.append({
                "ID": str(s.ID),
                "Temp (°C)": float(round(s.T - 273.15, 2)),
                "Flujo (kg/h)": float(round(s.F_mass, 2)),
                "% Etanol": f"{(s.imass['Ethanol']/s.F_mass)*100:.1f}%"
            })
    datos_en = []
    for u in sistema.units:
        calor_kw = sum(hu.duty for hu in u.heat_utilities) / 3600
        potencia = u.power_utility.rate if hasattr(u, "power_utility") and u.power_utility else 0.0
        if abs(calor_kw) > 0.01 or potencia > 0.01:
            datos_en.append({"Equipo": str(u.ID), "Calor (kW)": float(round(calor_kw, 2)), "Potencia (kW)": float(round(potencia, 2))})
    return pd.DataFrame(datos_mat), pd.DataFrame(datos_en)

# =================================================================
# 4. INTERFAZ DE USUARIO (LAYOUT)
# =================================================================
st.title("⚙️ Simulador Técnico-Económico de Procesos")

st.sidebar.header("🎛️ 1. Parámetros de Operación")
f_agua = st.sidebar.number_input("Flujo Agua (kg/h)", 500, 2000, 900)
f_etanol = st.sidebar.number_input("Flujo Etanol (kg/h)", 10, 500, 100)

# Sliders enlazados al session_state para control bidireccional
st.sidebar.slider("Temp. Alimentación Mosto (°C)", 15, 60, key="t_entrada")
st.sidebar.slider("Temp. Salida W220 (°C)", 86, 110, key="t_w220")
st.sidebar.slider("Presión de Separador V100 (Pa)", 10000, 200000, step=5000, key="p_sep")

st.sidebar.divider()
st.sidebar.header("💰 2. Parámetros Económicos")
p_luz = st.sidebar.slider("Precio Luz ($/kWh)", 0.01, 0.20, 0.085, format="%.3f")
p_vapor = st.sidebar.slider("Precio Vapor ($/MJ)", 0.005, 0.100, 0.025, format="%.3f")
p_agua = st.sidebar.number_input("Precio Agua Enf. ($/MJ)", min_value=0.0001, max_value=0.0050, value=0.0005, step=0.0001, format="%.4f")
p_mosto = st.sidebar.number_input("Costo Mosto ($/kg)", min_value=0.0000001, max_value=0.0001000, value=0.0000005, step=0.0000001, format="%.7f")
p_etanol = st.sidebar.slider("Precio de Venta Etanol ($/kg)", 0.5, 3.0, 1.2, format="%.2f")

st.sidebar.divider()
tutor_mode = st.sidebar.checkbox("🤖 Habilitar Modo Tutor IA")

# Ejecución Automática al inicio o al mover parámetros
sys, prod_unit, npv, roi, pbp, costo_prod, precio_sug = run_simulation(
    f_agua, f_etanol, st.session_state.t_entrada, st.session_state.t_w220, st.session_state.p_sep, 
    p_luz, p_vapor, p_agua, p_mosto, p_etanol
)
df_mat, df_en = generar_tablas(sys)
producto_final = prod_unit.outs[0]

# Actualizar el historial para graficar (Punto 16)
nueva_data = pd.DataFrame([{
    "Iteración": st.session_state.iteracion, "Temp_Mosto": st.session_state.t_entrada, 
    "Temp_W220": st.session_state.t_w220, "Presion_V100": st.session_state.p_sep, 
    "NPV": npv, "Costo_Prod": costo_prod
}])
st.session_state.sim_history = pd.concat([st.session_state.sim_history, nueva_data], ignore_index=True).drop_duplicates(subset=['Temp_Mosto', 'Temp_W220', 'Presion_V100'], keep='last')
st.session_state.iteracion += 1

# Pestañas Dinámicas
tabs_titles = ["⚙️ Simulación", "🗂️ DB (ISO)", "📐 PFD (ISO)"]
if tutor_mode:
    tabs_titles.append("💬 Contexto y Tutor IA")

tabs = st.tabs(tabs_titles)

with tabs[0]:
    st.subheader("🧪 Propiedades del Producto Final")
    t1, t2, t3, t4 = st.columns(4)
    flujo_masa = producto_final.F_mass
    t1.metric("Presión", f"{producto_final.P / 100000:.2f} bar")
    t2.metric("Temperatura", f"{producto_final.T - 273.15:.1f} °C")
    t3.metric("Flujo Másico", f"{flujo_masa:.2f} kg/h")
    t4.metric("Composición (Etanol)", f"{(producto_final.imass['Ethanol'] / flujo_masa) * 100 if flujo_masa > 0 else 0:.1f} %")

    st.divider()
    st.subheader("📈 Indicadores Financieros (TEA)")
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Costo Real Producción", f"${costo_prod:.2f} /kg")
    e2.metric("Precio Venta Sugerido", f"${precio_sug:.2f} /kg")
    e3.metric("NPV", f"${npv:,.0f}")
    e4.metric("Payback", f"{pbp:.1f} años")
    e5.metric("ROI", f"{roi:.1f} %")

    st.divider()
    col_mat, col_en = st.columns(2)
    with col_mat:
        st.markdown("### Balance de Materia")
        st.dataframe(df_mat, use_container_width=True)
    with col_en:
        st.markdown("### Balance de Energía")
        st.dataframe(df_en, use_container_width=True)

with tabs[1]:
    st.markdown("### 🗂️ Diagrama de Bloques (ISO)")
    manejar_pdf("DB.pdf")

with tabs[2]:
    st.markdown("### 📐 Diagrama de Flujo de Proceso (ISO)")
    manejar_pdf("DFP.pdf")

if tutor_mode:
    with tabs[3]:
        st.markdown("### 💬 Asistente IA Interactivo")
        st.markdown("Puedes pedirme que analice los datos o que **modifique directamente la Temperatura del Mosto, la Salida W220 o la Presión del Flash**.")
        
        # Gráfica interactiva de historial (Punto 16)
        if not st.session_state.sim_history.empty:
            st.markdown("📊 **Evolución del Proyecto por cada cambio de variable**")
            st.line_chart(st.session_state.sim_history.set_index("Iteración")[["NPV", "Costo_Prod"]])
            st.divider()

        # Interfaz de Chat
        for msg in st.session_state.chat_history:
            st.chat_message(msg["role"]).write(msg["content"])

        prompt_usuario = st.chat_input("Ej: Explica el NPV, o 'Sube la temperatura del mosto a 35'")
        
        if prompt_usuario:
            st.session_state.chat_history.append({"role": "user", "content": prompt_usuario})
            st.chat_message("user").write(prompt_usuario)

            if "GEMINI_API_KEY" in st.secrets:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                model = genai.GenerativeModel('gemini-2.5-pro')
                
                # Prompt del sistema oculto para dotar de poderes a la IA
                system_context = f"""
                Eres un tutor de ingeniería química. El estado actual de la simulación es: 
                Temp. Mosto: {st.session_state.t_entrada}°C, Temp. W220: {st.session_state.t_w220}°C, Presión: {st.session_state.p_sep}Pa. 
                Resultados -> NPV: ${npv:,.0f}, Costo: ${costo_prod:.2f}/kg.
                
                INSTRUCCIÓN CRÍTICA (PUNTO 16): Si el usuario te pide modificar la temperatura del mosto, la temperatura del W220 o la presión del separador, DEBES incluir al final de tu respuesta un bloque JSON exacto con las nuevas variables. 
                Ejemplo formato: 
http://googleusercontent.com/immersive_entry_chip/0
