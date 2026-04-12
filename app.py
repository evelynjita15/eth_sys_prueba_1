import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# =================================================================
# 1. CONFIGURACIÓN DE LA PÁGINA Y ESTILOS
# =================================================================
st.set_page_config(page_title="Simulador de Procesos y TEA", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #000000; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

# =================================================================
# 2. CLASE DE INGENIERÍA ECONÓMICA (TEA DIDÁCTICO)
# =================================================================
class TEA_Didactico(bst.TEA):
    def _DPI(self, installed_equipment_cost):
        return self.purchase_cost

    def _TDC(self, DPI):
        return DPI

    def _FCI(self, TDC):
        return self.purchase_cost * self.lang_factor

    def _TCI(self, FCI):
        return FCI + self.WC

    def _FOC(self, FCI):
        return 0.0

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
    
    # Configuración Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Definición de Corrientes (Asignando precio inicial a la materia prima)
    mosto = bst.Stream("MOSTO", Water=w_flow, Ethanol=e_flow, units="kg/hr", T=t_in + 273.15, P=101325)
    mosto.price = p_mosto
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15, P=300000)

    # Selección de Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("Mosto_Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=t_w220 + 273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=p_flash)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Vinazas"), P=p_flash, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Precios de Servicios Auxiliares (Utilities)
    bst.PowerUtility.price = p_luz
    vapor = bst.HeatUtility.get_agent("low_pressure_steam")
    vapor.heat_transfer_price = p_vapor
    agua = bst.HeatUtility.get_agent("cooling_water")
    agua.heat_transfer_price = p_agua

    # Simulación
    sys = bst.System("sys_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()

    # Evaluación Económica (TEA)
    producto = W310.outs[0]
    producto.price = p_etanol # Asignamos el precio del slider al producto

    tea = TEA_Didactico(
        system=sys, IRR=0.15, duration=(2025, 2045), income_tax=0.3,
        depreciation="MACRS7", construction_schedule=(0.4, 0.6),
        startup_months=6, startup_FOCfrac=0.5, startup_VOCfrac=0.5,
        startup_salesfrac=0.5, operating_days=330, lang_factor=4.0,
        WC_over_FCI=0.05, finance_interest=0.0, finance_years=0.0,
        finance_fraction=0.0
    )

    # Extraer indicadores con el precio actual del slider
    npv_actual = tea.NPV
    roi_actual = tea.ROI * 100 # Convertir a porcentaje
    pbp_actual = tea.PBP

    # Caso A: Costo Real de Producción (Ganancia = 0)
    tea.IRR = 0.0
    costo_prod = tea.solve_price(producto)

    # Caso B: Precio Meta Sugerido (Rendimiento 15%)
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
            datos_en.append({
                "Equipo": str(u.ID),
                "Calor (kW)": float(round(calor_kw, 2)),
                "Potencia (kW)": float(round(potencia, 2))
            })
            
    return pd.DataFrame(datos_mat), pd.DataFrame(datos_en)

# =================================================================
# 4. INTERFAZ DE USUARIO (LAYOUT)
# =================================================================
st.title("⚙️ Simulador Técnico-Económico de Procesos")

st.sidebar.header("🎛️ 1. Parámetros de Operación")
f_agua = st.sidebar.number_input("Flujo Agua (kg/h)", 500, 2000, 900)
f_etanol = st.sidebar.number_input("Flujo Etanol (kg/h)", 10, 500, 100)
t_entrada = st.sidebar.slider("Temp. Alimentación Mosto (°C)", 15, 60, 25)
t_w220_out = st.sidebar.slider("Temp. Salida W220 (°C)", 86, 110, 92)
p_sep = st.sidebar.slider("Presión de Separador V100 (Pa)", 10000, 200000, 101325, step=5000)

st.sidebar.divider()

st.sidebar.header("💰 2. Parámetros Económicos")
p_luz = st.sidebar.slider("Precio Luz ($/kWh)", 0.01, 0.20, 0.085, format="%.3f")
p_vapor = st.sidebar.slider("Precio Vapor ($/MJ)", 0.005, 0.100, 0.025, format="%.3f")
p_agua = st.sidebar.slider("Precio Agua Enf. ($/MJ)", 0.0001, 0.0050, 0.0005, step=0.0001, format="%.4f")
p_mosto = st.sidebar.number_input("Costo Mosto ($/kg)", min_value=0.0000001, max_value=0.0001000, value=0.0000005, step=0.0000001, format="%.7f")
p_etanol = st.sidebar.slider("Precio de Venta Etanol ($/kg)", 0.5, 3.0, 1.2, format="%.2f")

if st.sidebar.button("🚀 Ejecutar Simulación"):
    # Ejecución
    sys, prod_unit, npv, roi, pbp, costo_prod, precio_sug = run_simulation(
        f_agua, f_etanol, t_entrada, t_w220_out, p_sep, 
        p_luz, p_vapor, p_agua, p_mosto, p_etanol
    )
    df_mat, df_en = generar_tablas(sys)
    producto_final = prod_unit.outs[0] # Corriente de salida
    # --- MÉTRICAS DE LA CORRIENTE DE PRODUCTO ---
    st.subheader("🧪 Propiedades del Producto Final")
    t1, t2, t3, t4 = st.columns(4)
    
    presion_bar = producto_final.P / 100000
    temp_c = producto_final.T - 273.15
    flujo_masa = producto_final.F_mass
    comp_etanol = (producto_final.imass['Ethanol'] / flujo_masa) * 100 if flujo_masa > 0 else 0

    t1.metric("Presión", f"{presion_bar:.2f} bar")
    t2.metric("Temperatura", f"{temp_c:.1f} °C")
    t3.metric("Flujo Másico", f"{flujo_masa:.2f} kg/h")
    t4.metric("Composición (Etanol)", f"{comp_etanol:.1f} %")

    st.divider()

    # --- MÉTRICAS ECONÓMICAS (TEA) ---
    st.subheader("📈 Indicadores Financieros (TEA)")
    e1, e2, e3, e4, e5 = st.columns(5)
    
    e1.metric("Costo Real Producción", f"${costo_prod:.2f} /kg")
    e2.metric("Precio Venta Sugerido", f"${precio_sug:.2f} /kg")
    e3.metric("NPV (Valor Presente)", f"${npv:,.0f}")
    e4.metric("Payback (Retorno)", f"{pbp:.1f} años")
    e5.metric("ROI", f"{roi:.1f} %")

    st.divider()

    # --- TABLAS DE MATERIA Y ENERGÍA ---
    col_mat, col_en = st.columns(2)
    with col_mat:
        st.markdown("### Balance de Materia")
        st.dataframe(df_mat, use_container_width=True)
    with col_en:
        st.markdown("### Balance de Energía")
        st.dataframe(df_en, use_container_width=True)

    # --- DIAGRAMA Y ASISTENTE IA ---
    st.divider()
    col_pfd, col_ia = st.columns([1, 1])

    with col_pfd:
        st.markdown("### 📐 Diagrama del Proceso")
        try:
            sys.diagram(format='png', file='pfd_temp', display=False)
            st.image('pfd_temp.png')
        except Exception:
            st.info("El diagrama requiere Graphviz configurado en el servidor.")

    with col_ia:
        st.markdown("### 🤖 Asistente Económico")
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-2.5-pro')
            
            contexto = f"NPV: ${npv:,.0f}, Payback: {pbp:.1f} años, ROI: {roi:.1f}%. Precio de venta actual: ${p_etanol}/kg vs Costo: ${costo_prod:.2f}/kg."
            prompt = f"Actúa como un gerente financiero de planta. Analiza estos resultados económicos de la simulación y da 2 consejos para mejorar la rentabilidad (sé muy directo y breve): {contexto}"
            
            with st.spinner("Analizando finanzas..."):
                try:
                    response = model.generate_content(prompt)
                    st.write(response.text)
                except Exception:
                    st.error("Hubo un problema de conexión con la IA.")
        else:
            st.info("Configura GEMINI_API_KEY en los Secrets de Streamlit para activar el asistente.")
