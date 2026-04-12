import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# =================================================================
# 1. CONFIGURACIÓN DE LA PÁGINA Y ESTILOS
# =================================================================
st.set_page_config(page_title="Simulador de Separación", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

# =================================================================
# 2. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# =================================================================
def run_simulation(w_flow, e_flow, t_in, t_w220, p_flash):
    # Limpiar flujos previos para evitar errores de ID duplicado
    bst.main_flowsheet.clear()
    
    # Configuración Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Definición de Corrientes
    mosto = bst.Stream("MOSTO", Water=w_flow, Ethanol=e_flow, units="kg/hr", T=t_in + 273.15, P=101325)
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15, P=300000)

    # Selección de Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("Mosto_Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    
    # NUEVO: La temperatura de salida ahora es un parámetro dinámico (t_w220)
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=t_w220 + 273.15)
    
    # NUEVO: La presión de la válvula y el flash ahora es un parámetro dinámico (p_flash)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=p_flash)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Vinazas"), P=p_flash, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Simulación del Sistema
    sys = bst.System("sys_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    return sys, V1, W310

def generar_tablas(sistema):
    # Tabla de Materia (Asegurando formatos simples para evitar errores de renderizado)
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0.1:
            datos_mat.append({
                "ID Corriente": str(s.ID),
                "Temp (°C)": float(round(s.T - 273.15, 2)),
                "Flujo (kg/h)": float(round(s.F_mass, 2)),
                "% Etanol": f"{(s.imass['Ethanol']/s.F_mass)*100:.1f}%"
            })
    
    # Tabla de Energía (Asegurando formatos simples)
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
# 3. INTERFAZ DE USUARIO (LAYOUT)
# =================================================================
st.title("⚙️ Simulador Interactivo de Separación")
st.sidebar.header("🎛️ Parámetros de Operación")

# --- Controles de Flujo ---
st.sidebar.subheader("Flujos de Entrada")
f_agua = st.sidebar.number_input("Flujo Agua (kg/h)", min_value=500, max_value=2000, value=900)
f_etanol = st.sidebar.number_input("Flujo Etanol (kg/h)", min_value=10, max_value=500, value=100)

st.sidebar.divider()

# --- Los 3 Sliders Obligatorios ---
st.sidebar.subheader("Condiciones Termodinámicas")

# 1. Slider para la temperatura de alimentación del mosto
t_entrada = st.sidebar.slider("1. Temp. Alimentación Mosto (°C)", min_value=15, max_value=60, value=25)

# 2. Slider para la temperatura de salida de W220
t_w220_out = st.sidebar.slider("2. Temp. Salida Calentador W220 (°C)", min_value=86, max_value=110, value=92)

# 3. Slider para la presión del separador V100/V1
p_sep = st.sidebar.slider("3. Presión de Separación (Pa)", min_value=10000, max_value=200000, value=101325, step=5000)

st.sidebar.divider()

if st.sidebar.button("🚀 Iniciar Simulación"):
    # Ejecutamos la simulación pasando los nuevos parámetros
    sys, flash_unit, prod_unit = run_simulation(f_agua, f_etanol, t_entrada, t_w220_out, p_sep)
    df_mat, df_en = generar_tablas(sys)
    
    # --- SECCIÓN DE KPIS ---
    st.subheader("📊 Resultados Principales")
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    
    # Cálculos para los indicadores
    flujo_total_salida = prod_unit.outs[0].F_mass
    masa_etanol_salida = prod_unit.outs[0].imass['Ethanol']
    
    pureza = (masa_etanol_salida / flujo_total_salida) * 100 if flujo_total_salida > 0 else 0
    recuperacion = (masa_etanol_salida / f_etanol) * 100 if f_etanol > 0 else 0
    energia_total = df_en["Calor (kW)"].abs().sum() if not df_en.empty else 0
    
    kpi1.metric("Pureza Etanol", f"{pureza:.2f} %")
    kpi2.metric("Recuperación", f"{recuperacion:.2f} %")
    kpi3.metric("Consumo Térmico", f"{energia_total:.2f} kW")
    kpi4.metric("Estado", "✅ Listo")

    st.divider()

    # --- SECCIÓN DE TABLAS LADO A LADO ---
    col_mat, col_en = st.columns(2)
    
    with col_mat:
        st.markdown("### 🧪 Balance de Materia")
        st.dataframe(df_mat, use_container_width=True)
        
    with col_en:
        st.markdown("### ⚡ Balance de Energía")
        st.dataframe(df_en, use_container_width=True)

    # --- SECCIÓN DE IA Y PFD ---
    st.divider()
    col_pfd, col_ia = st.columns([1, 1])

    with col_pfd:
        st.markdown("### 📐 Diagrama del Proceso")
        try:
            sys.diagram(format='png', file='pfd_temp', display=False)
            st.image('pfd_temp.png')
        except Exception as e:
            st.info("El diagrama se mostrará cuando la librería Graphviz esté configurada en el servidor.")

    with col_ia:
        st.markdown("### 🤖 Asistente de Optimización")
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-2.5-pro')
            
            contexto = f"Pureza: {pureza:.1f}%, Energía: {energia_total:.1f}kW. T. Salida W220: {t_w220_out}°C, Presión Flash: {p_sep}Pa."
            prompt = f"Analiza estos datos de simulación de un proceso químico y da 2 consejos prácticos para optimizar la energía basándote en la presión y temperatura dadas: {contexto}"
            
            with st.spinner("Analizando..."):
                try:
                    response = model.generate_content(prompt)
                    st.write(response.text)
                except Exception as e:
                    st.error("Hubo un problema de conexión con la IA.")
        else:
            st.info("Falta vincular la clave de la IA en la configuración de Streamlit Cloud.")
