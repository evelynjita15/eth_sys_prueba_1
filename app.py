import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
import os

# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA
# ==========================================
st.set_page_config(page_title="Simulador BioSTEAM", layout="wide", page_icon="⚗️")
st.title("⚗️ Simulador de Planta de Etanol")
st.markdown("Plataforma interactiva para el análisis de balances de materia y energía con tutoría de IA integrada.")
st.markdown("---")

# ==========================================
# 2. IA: CONFIGURACIÓN DE GEMINI
# ==========================================
api_key = st.secrets.get("GEMINI_API_KEY") if st.secrets else None
if api_key:
    genai.configure(api_key=api_key)
    modelo_ia = genai.GenerativeModel('gemini-2.5-pro') 

# ==========================================
# 3. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# ==========================================
@st.cache_data(show_spinner=False) # Caché para optimizar recargas
def ejecutar_simulacion(flujo_agua, flujo_etanol, temp_mosto, temp_calentador):
    bst.main_flowsheet.clear()
    
    # Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes
    mosto = bst.Stream("1-MOSTO", Water=flujo_agua, Ethanol=flujo_etanol, units="kg/hr", T=temp_mosto+273.15, P=101325)
    vinazas_retorno = bst.Stream("Vinazas-Retorno", Water=200, Ethanol=0, units="kg/hr", T=95+273.15, P=300000)

    # Equipos
    P100 = bst.Pump("P-100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W-210", ins=(P100-0, vinazas_retorno), outs=("3-Mosto-Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    W220 = bst.HXutility("W-220", ins=W210-0, outs="Mezcla", T=temp_calentador+273.15)
    V100 = bst.IsenthalpicValve("V-100", ins=W220-0, outs="Mezcla-Bifásica", P=101325)
    V1 = bst.Flash("V-1", ins=V100-0, outs=("Producto Final", "Vinazas"), P=101325, Q=0) # Nombre de salida ajustado
    W310 = bst.HXutility("W-310", ins=V1-0, outs="Vapor Condensado", T=25 + 273.15)
    P200 = bst.Pump("P-200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    eth_sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    
    try:
        eth_sys.simulate()
        estado = "✅ Convergencia exitosa"
    except Exception as e:
        estado = f"⚠ Error de convergencia: {e}"

    # Extracción de Datos para Tablas
    datos_mat = []
    flujo_producto = 0
    pureza_producto = 0

    for s in eth_sys.streams:
        if s.F_mass > 0:
            fraccion_etanol = s.imass['Ethanol'] / s.F_mass
            datos_mat.append({
                "ID": s.ID,
                "T (°C)": round(s.T-273.15, 2),
                "P (bar)": round(s.P/1e5, 2),
                "Flujo (kg/h)": round(s.F_mass, 2),
                "% Etanol": f"{fraccion_etanol:.1%}"
            })
            # Capturamos datos específicos para los KPIs
            if s.ID == "Producto Final":
                flujo_producto = s.F_mass
                pureza_producto = fraccion_etanol

    df_mat = pd.DataFrame(datos_mat)

    datos_en = []
    energia_total_kw = 0

    for u in eth_sys.units:
        calor_kw = 0.0
        tipo = "-"
        
        if isinstance(u, bst.HXprocess):
            calor_kw = (u.outs[0].H - u.ins[0].H) / 3600
            tipo = "Recuperación"
        elif hasattr(u, "duty") and u.duty is not None and not isinstance(u, bst.Flash):
            calor_kw = u.duty / 3600
            tipo = "Vapor" if calor_kw > 0.01 else "Enfriamiento"

        if abs(calor_kw) > 0.01:
            datos_en.append({"Equipo": u.ID, "Función": tipo, "Energía (kW)": round(calor_kw, 2)})
            if tipo != "Recuperación": # Solo sumamos la energía externa para el KPI
                energia_total_kw += abs(calor_kw)

    df_en = pd.DataFrame(datos_en)

    # Renderizado del diagrama
    diagrama_path = "diagrama.png"
    eth_sys.diagram(file=diagrama_path.replace(".png", ""), format="png")

    # Retornamos también los valores para los KPIs
    return df_mat, df_en, diagrama_path, estado, flujo_producto, pureza_producto, energia_total_kw

# ==========================================
# 4. INTERFAZ DE USUARIO (UI / DASHBOARD)
# ==========================================
st.sidebar.header("🎛️ Parámetros de Operación")
flujo_agua = st.sidebar.slider("Flujo Agua (kg/h)", 500, 1500, 900, step=50)
flujo_etanol = st.sidebar.slider("Flujo Etanol (kg/h)", 50, 300, 100, step=10)
temp_mosto = st.sidebar.slider("Temperatura Mosto Inicial (°C)", 10, 40, 25, step=1)
temp_calentador = st.sidebar.slider("Temp. Calentador W-220 (°C)", 85, 100, 92, step=1)

if st.sidebar.button("▶️ Ejecutar Simulación", type="primary"):
    with st.spinner("Calculando balances termodinámicos..."):
        # Ejecutamos la simulación
        df_mat, df_en, diagrama, estado, f_prod, pureza, e_total = ejecutar_simulacion(
            flujo_agua, flujo_etanol, temp_mosto, temp_calentador
        )
        
        st.toast(estado) # Notificación sutil de éxito
        
        # --- SECCIÓN 1: KPIs ---
        st.subheader("📊 Indicadores de Rendimiento (KPIs)")
        kpi1, kpi2, kpi3 = st.columns(3)
        
        kpi1.metric(
            label="Pureza del Destilado", 
            value=f"{pureza:.1%}", 
            delta="Objetivo > 40%" if pureza > 0.4 else "Baja pureza",
            delta_color="normal" if pureza > 0.4 else "inverse"
        )
        kpi2.metric(
            label="Flujo de Destilado", 
            value=f"{f_prod:.1f} kg/h"
        )
        kpi3.metric(
            label="Demanda Energética Externa", 
            value=f"{e_total:.1f} kW",
            help="Suma de las necesidades de calentamiento y enfriamiento externo."
        )
        
        st.markdown("<br>", unsafe_allow_html=True) # Espaciado

        # --- SECCIÓN 2: TABLAS SIDE-BY-SIDE ---
        col_mat, col_en = st.columns(2)
        
        with col_mat:
            st.subheader("💧 Balance de Materia")
            st.dataframe(df_mat, use_container_width=True, hide_index=True)
            
        with col_en:
            st.subheader("🔥 Balance de Energía")
            st.dataframe(df_en, use_container_width=True, hide_index=True)

        st.markdown("---")
        
        # --- SECCIÓN 3: PFD Y TUTOR IA ---
        col_diagrama, col_ia = st.columns([1.2, 1]) # La columna del diagrama es un poco más ancha
        
        with col_diagrama:
            st.subheader("🗺️ Diagrama de Flujo (PFD)")
            if os.path.exists(diagrama):
                st.image(diagrama, use_container_width=True)
                
        with col_ia:
            st.subheader("🧠 Análisis del Tutor IA")
            if api_key:
                prompt = f"""
                Actúa como un tutor experto en ingeniería química. Resultados del flash etanol-agua:
                - Pureza lograda: {pureza:.1%}
                - Energía externa requerida: {e_total:.1f} kW
                Explica brevemente la relación entre la temperatura del calentador y la pureza obtenida, y si la separación fue eficiente. Usa un tono analítico y claro.
                """
                respuesta = modelo_ia.generate_content(prompt)
                st.info(respuesta.text)
            else:
                st.warning("Configura tu GEMINI_API_KEY en Streamlit Secrets (o localmente) para habilitar el tutor con IA.")
else:
    # Pantalla de inicio antes de simular
    st.info("👈 Ajusta los parámetros en el panel lateral y presiona 'Ejecutar Simulación' para comenzar.")
