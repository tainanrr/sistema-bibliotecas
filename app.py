import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import hashlib
import time
import io

# ==============================================================================
# CONFIGURAÇÕES GERAIS E ESTILO
# ==============================================================================
st.set_page_config(
    page_title="SGBC - Rede Estadual",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Constantes de Regra de Negócio (Conforme Dossiê)
PRAZO_PADRAO_DIAS = 14
LIMITE_LIVROS_POR_LEITOR = 3
LIMITE_RENOVACOES = 2
DB_FILE = "sgbc_rede_estadual.db"

# ==============================================================================
# CAMADA DE DADOS (DATABASE & PERSISTÊNCIA)
# ==============================================================================

def get_connection():
    """Cria conexão com thread check desligado para Streamlit Cloud"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    return conn

def init_db():
    """Inicializa o Schema do Banco de Dados conforme Dossiê"""
    conn = get_connection()
    c = conn.cursor()

    # 1. Bibliotecas (Unidades)
    c.execute('''CREATE TABLE IF NOT EXISTS libraries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        city TEXT,
        address TEXT,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # 2. Usuários (Staff e Leitores)
    # Role: 'admin_rede', 'coord_local', 'leitor'
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        document TEXT, -- CPF/RG (Opcional)
        phone TEXT,
        password TEXT, -- Hash (Staff)
        role TEXT NOT NULL,
        library_id INTEGER, -- Biblioteca de origem/vínculo
        active INTEGER DEFAULT 1,
        lgpd_consent INTEGER DEFAULT 0, -- 1=Aceitou
        blocked_until DATE, -- Bloqueio por atraso
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(library_id) REFERENCES libraries(id)
    )''')

    # 3. Obras (Catálogo Bibliográfico Único da Rede)
    c.execute('''CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        isbn TEXT,
        publisher TEXT,
        category TEXT,
        year INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # 4. Exemplares (Itens Físicos nas Bibliotecas)
    # Status: 'disponivel', 'emprestado', 'reservado', 'manutencao', 'extraviado'
    c.execute('''CREATE TABLE IF NOT EXISTS copies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER,
        library_id INTEGER,
        code TEXT NOT NULL, -- Código de barras/Etiqueta
        status TEXT DEFAULT 'disponivel',
        acquired_at DATE,
        FOREIGN KEY(book_id) REFERENCES books(id),
        FOREIGN KEY(library_id) REFERENCES libraries(id),
        UNIQUE(library_id, code)
    )''')

    # 5. Circulação (Empréstimos)
    c.execute('''CREATE TABLE IF NOT EXISTS loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        copy_id INTEGER,
        library_id INTEGER, -- Onde ocorreu o empréstimo
        loan_date DATE,
        due_date DATE,
        return_date DATE,
        renewals_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'aberto', -- 'aberto', 'devolvido'
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(copy_id) REFERENCES copies(id)
    )''')

    # 6. Auditoria (Logs LGPD)
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        user_id INTEGER, -- Quem fez a ação
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Seed Inicial (Admin)
    c.execute("SELECT * FROM users WHERE role='admin_rede'")
    if not c.fetchone():
        # Senha padrão: admin123
        pass_hash = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("INSERT INTO users (name, email, password, role, active) VALUES (?, ?, ?, ?, 1)", 
                  ("Administração Central", "admin@rede.com", pass_hash, "admin_rede"))
        c.execute("INSERT INTO libraries (name, city) VALUES (?, ?)", ("Biblioteca Central (Sede)", "Capital"))
        
    conn.commit()
    conn.close()

# Inicializa banco
init_db()

# ==============================================================================
# FUNÇÕES AUXILIARES (LÓGICA DE NEGÓCIO)
# ==============================================================================

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def log_audit(action, details):
    """Registra ações críticas para conformidade LGPD"""
    if st.session_state.user:
        user_id = st.session_state.user['id']
        conn = get_connection()
        conn.execute("INSERT INTO audit_logs (action, user_id, details) VALUES (?, ?, ?)", 
                     (action, user_id, str(details)))
        conn.commit()
        conn.close()

def check_leitor_elegivel(user_id):
    """Verifica regras: Bloqueio por atraso e Limite de itens"""
    conn = get_connection()
    
    # 1. Verificar bloqueio
    user = pd.read_sql(f"SELECT active, blocked_until FROM users WHERE id={user_id}", conn).iloc[0]
    if user['active'] == 0:
        return False, "Usuário inativo."
    
    if user['blocked_until'] and user['blocked_until'] >= datetime.now().strftime('%Y-%m-%d'):
        return False, f"Usuário bloqueado até {user['blocked_until']} por atrasos anteriores."

    # 2. Verificar atrasos atuais (itens não devolvidos e vencidos)
    hoje = datetime.now().strftime('%Y-%m-%d')
    atrasos = pd.read_sql(f"SELECT count(*) FROM loans WHERE user_id={user_id} AND status='aberto' AND due_date < '{hoje}'", conn).iloc[0,0]
    if atrasos > 0:
        return False, "Usuário possui itens em atraso. Regularize antes de novos empréstimos."

    # 3. Verificar limite de quantidade
    ativos = pd.read_sql(f"SELECT count(*) FROM loans WHERE user_id={user_id} AND status='aberto'", conn).iloc[0,0]
    if ativos >= LIMITE_LIVROS_POR_LEITOR:
        return False, f"Limite de empréstimos atingido ({LIMITE_LIVROS_POR_LEITOR} itens)."

    conn.close()
    return True, "Elegível"

# ==============================================================================
# INTERFACE DE USUÁRIO (FRONTEND)
# ==============================================================================

def login_sidebar():
    """Gerencia autenticação na barra lateral"""
    with st.sidebar:
        st.title("🏛️ SGBC Rede")
        
        if 'user' not in st.session_state:
            st.session_state.user = None

        if st.session_state.user:
            u = st.session_state.user
            st.success(f"👤 {u['name']}")
            st.caption(f"Função: {u['role'].upper()}")
            if u['library_name']:
                st.caption(f"📍 {u['library_name']}")
            
            if st.button("Sair / Logout", type="primary"):
                st.session_state.user = None
                st.rerun()
            
            # Botão de Backup Crítico
            st.divider()
            with open(DB_FILE, "rb") as f:
                st.download_button(
                    label="💾 BACKUP DE DADOS",
                    data=f,
                    file_name=f"backup_sgbc_{datetime.now().strftime('%Y%m%d')}.db",
                    mime="application/x-sqlite3",
                    help="Baixe semanalmente para evitar perda de dados no servidor gratuito."
                )
        else:
            with st.form("login_form"):
                st.markdown("### Acesso Restrito")
                email = st.text_input("E-mail Institucional")
                pwd = st.text_input("Senha", type="password")
                if st.form_submit_button("Entrar"):
                    conn = get_connection()
                    c = conn.cursor()
                    # Join para pegar nome da biblioteca se existir
                    query = """
                        SELECT u.id, u.name, u.role, u.library_id, l.name as library_name 
                        FROM users u 
                        LEFT JOIN libraries l ON u.library_id = l.id 
                        WHERE u.email=? AND u.password=? AND u.active=1
                    """
                    c.execute(query, (email, hash_pass(pwd)))
                    data = c.fetchone()
                    conn.close()
                    
                    if data:
                        st.session_state.user = {
                            "id": data[0], "name": data[1], "role": data[2], 
                            "library_id": data[3], "library_name": data[4]
                        }
                        st.rerun()
                    else:
                        st.error("Acesso negado.")

# ==============================================================================
# MÓDULOS DO SISTEMA
# ==============================================================================

def page_public_search():
    """Módulo 1: Catálogo Público (Sem Login)"""
    st.header("🔍 Catálogo da Rede Estadual")
    st.markdown("Pesquise disponibilidade de obras em todas as bibliotecas cadastradas.")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        termo = st.text_input("Digite Título, Autor ou ISBN", placeholder="Ex: Dom Casmurro...")
    with col2:
        conn = get_connection()
        libs = pd.read_sql("SELECT id, name FROM libraries WHERE active=1", conn)
        filtro_lib = st.selectbox("Filtrar por Biblioteca", ["Todas"] + libs['name'].tolist())
        conn.close()

    if termo:
        conn = get_connection()
        query = """
            SELECT b.title, b.author, b.category, l.name as library, c.status
            FROM copies c
            JOIN books b ON c.book_id = b.id
            JOIN libraries l ON c.library_id = l.id
            WHERE (b.title LIKE ? OR b.author LIKE ?)
        """
        params = [f'%{termo}%', f'%{termo}%']
        
        if filtro_lib != "Todas":
            query += " AND l.name = ?"
            params.append(filtro_lib)
            
        df = pd.read_sql(query, conn, params=params)
        conn.close()

        if not df.empty:
            for _, row in df.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1])
                    c1.subheader(row['title'])
                    c1.text(f"Autor: {row['author']} | Gênero: {row['category']}")
                    
                    status_color = "green" if row['status'] == 'disponivel' else "red"
                    c2.markdown(f"📍 **{row['library']}**")
                    c2.markdown(f":{status_color}[{row['status'].upper()}]")
        else:
            st.info("Nenhum item encontrado com esses termos.")

def page_admin_dashboard():
    """Módulo 2: Painel da Rede (Admin)"""
    st.title("📊 Painel de Controle da Rede")
    
    # KPIs
    conn = get_connection()
    total_lib = pd.read_sql("SELECT count(*) FROM libraries", conn).iloc[0,0]
    total_books = pd.read_sql("SELECT count(*) FROM books", conn).iloc[0,0]
    total_loans = pd.read_sql("SELECT count(*) FROM loans WHERE status='aberto'", conn).iloc[0,0]
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Bibliotecas Ativas", total_lib)
    c2.metric("Obras no Catálogo", total_books)
    c3.metric("Empréstimos em Curso", total_loans)
    
    st.divider()
    
    tab1, tab2, tab3 = st.tabs(["Bibliotecas", "Equipe & Acessos", "Catálogo Global"])
    
    with tab1:
        with st.expander("➕ Cadastrar Nova Biblioteca"):
            with st.form("new_lib"):
                name = st.text_input("Nome da Unidade")
                city = st.text_input("Cidade/Bairro")
                addr = st.text_input("Endereço Completo")
                if st.form_submit_button("Salvar"):
                    conn.execute("INSERT INTO libraries (name, city, address) VALUES (?, ?, ?)", (name, city, addr))
                    conn.commit()
                    log_audit("create_library", f"Criou biblioteca {name}")
                    st.success("Biblioteca criada!")
                    st.rerun()
        st.dataframe(pd.read_sql("SELECT id, name, city, active FROM libraries", conn), use_container_width=True)

    with tab2:
        st.caption("Cadastre Coordenadores para as bibliotecas.")
        libs_df = pd.read_sql("SELECT id, name FROM libraries WHERE active=1", conn)
        with st.form("new_staff"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Nome Completo")
            email = c1.text_input("E-mail de Login")
            pwd = c2.text_input("Senha Inicial", type="password")
            lib_idx = c2.selectbox("Vincular à Biblioteca", libs_df['name'])
            
            if st.form_submit_button("Cadastrar Coordenador"):
                lib_id = libs_df[libs_df['name'] == lib_idx]['id'].values[0]
                try:
                    conn.execute("INSERT INTO users (name, email, password, role, library_id, active) VALUES (?, ?, ?, 'coord_local', ?, 1)",
                                 (nome, email, hash_pass(pwd), int(lib_id)))
                    conn.commit()
                    st.success("Usuário criado com sucesso!")
                except sqlite3.IntegrityError:
                    st.error("E-mail já cadastrado.")

    with tab3:
        st.info("Este é o cadastro bibliográfico único (título/autor) compartilhado por toda a rede.")
        with st.form("new_book_global"):
            c1, c2 = st.columns(2)
            tit = c1.text_input("Título da Obra")
            aut = c2.text_input("Autor(es)")
            cat = c1.text_input("Categoria/Gênero")
            isbn = c2.text_input("ISBN")
            if st.form_submit_button("Adicionar ao Catálogo Geral"):
                conn.execute("INSERT INTO books (title, author, category, isbn) VALUES (?, ?, ?, ?)", (tit, aut, cat, isbn))
                conn.commit()
                st.success("Obra adicionada! Agora as bibliotecas podem vincular exemplares.")
    
    conn.close()

def page_library_ops():
    """Módulo 3: Operação Local (Bibliotecário/Voluntário)"""
    user_lib_id = st.session_state.user['library_id']
    user_lib_name = st.session_state.user['library_name']
    
    st.title(f"📖 Gestão: {user_lib_name}")
    
    ops_tab = st.radio("Selecione a Operação", ["Balcão de Circulação", "Acervo (Exemplares)", "Leitores", "Relatórios"], horizontal=True)
    st.divider()

    conn = get_connection()

    # --- ABA BALCÃO ---
    if ops_tab == "Balcão de Circulação":
        c1, c2 = st.columns(2)
        
        # COLUNA 1: EMPRÉSTIMO
        with c1.container(border=True):
            st.subheader("📤 Novo Empréstimo")
            
            # Buscas otimizadas
            leitores = pd.read_sql(f"SELECT id, name, document FROM users WHERE role='leitor' AND active=1", conn)
            # Exemplares disponiveis APENAS DESTA BIBLIOTECA
            exemplares = pd.read_sql(f"""
                SELECT c.id, b.title, c.code 
                FROM copies c 
                JOIN books b ON c.book_id = b.id 
                WHERE c.library_id={user_lib_id} AND c.status='disponivel'
            """, conn)

            if not leitores.empty and not exemplares.empty:
                l_sel = st.selectbox("Leitor", leitores['name'] + " | Doc: " + leitores['document'].fillna(''))
                e_sel = st.selectbox("Livro Disponível", exemplares['title'] + " | Cód: " + exemplares['code'])
                
                if st.button("Confirmar Saída"):
                    # Identificar IDs
                    l_id = leitores[leitores['name'] == l_sel.split(" |")[0]]['id'].values[0]
                    e_code = e_sel.split(" | Cód: ")[1]
                    e_id = exemplares[exemplares['code'] == e_code]['id'].values[0]
                    
                    # Validar Regras
                    elegivel, msg = check_leitor_elegivel(l_id)
                    
                    if elegivel:
                        dt_hoje = datetime.now()
                        dt_prazo = dt_hoje + timedelta(days=PRAZO_PADRAO_DIAS)
                        
                        conn.execute("INSERT INTO loans (user_id, copy_id, library_id, loan_date, due_date) VALUES (?, ?, ?, ?, ?)",
                                     (int(l_id), int(e_id), user_lib_id, dt_hoje, dt_prazo))
                        conn.execute(f"UPDATE copies SET status='emprestado' WHERE id={e_id}")
                        conn.commit()
                        log_audit("loan_create", f"Empréstimo exemplar {e_code} para user {l_id}")
                        st.success(f"Empréstimo realizado! Devolução prevista: {dt_prazo.strftime('%d/%m/%Y')}")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(f"Bloqueado: {msg}")
            else:
                st.warning("Cadastre leitores e exemplares primeiro.")

        # COLUNA 2: DEVOLUÇÃO
        with c2.container(border=True):
            st.subheader("📥 Devolução")
            # Buscar empréstimos abertos DESTA biblioteca
            loans = pd.read_sql(f"""
                SELECT l.id, u.name, b.title, l.due_date, c.code
                FROM loans l 
                JOIN users u ON l.user_id = u.id
                JOIN copies c ON l.copy_id = c.id
                JOIN books b ON c.book_id = b.id
                WHERE l.library_id={user_lib_id} AND l.status='aberto'
            """, conn)
            
            if not loans.empty:
                loan_sel = st.selectbox("Selecione o Item Retornado", 
                                       f"{loans['title']} ({loans['code']}) - {loans['name']}")
                
                if st.button("Confirmar Devolução"):
                    # Parsing seleção
                    code_temp = loan_sel.split("(")[1].split(")")[0]
                    loan_id = loans[loans['code'] == code_temp]['id'].values[0]
                    
                    # Lógica de Atraso
                    loan_data = pd.read_sql(f"SELECT user_id, due_date, copy_id FROM loans WHERE id={loan_id}", conn).iloc[0]
                    dt_due = datetime.strptime(loan_data['due_date'], '%Y-%m-%d')
                    dt_now = datetime.now()
                    
                    msg_extra = ""
                    # Se atrasou, bloqueia
                    if dt_now > dt_due + timedelta(days=1): # Tolerancia de 1 dia
                        dias_atraso = (dt_now - dt_due).days
                        # Bloqueio simples: dias de atraso * 2
                        dt_unlock = dt_now + timedelta(days=dias_atraso * 2)
                        conn.execute("UPDATE users SET blocked_until=? WHERE id=?", (dt_unlock, int(loan_data['user_id'])))
                        msg_extra = f"⚠️ Atraso de {dias_atraso} dias. Leitor bloqueado até {dt_unlock.strftime('%d/%m/%Y')}."

                    conn.execute(f"UPDATE loans SET status='devolvido', return_date='{dt_now}' WHERE id={loan_id}")
                    conn.execute(f"UPDATE copies SET status='disponivel' WHERE id={loan_data['copy_id']}")
                    conn.commit()
                    log_audit("loan_return", f"Devolução id {loan_id}. {msg_extra}")
                    st.success(f"Devolução registrada! {msg_extra}")
                    time.sleep(3)
                    st.rerun()
            else:
                st.info("Nenhum empréstimo pendente nesta unidade.")

    # --- ABA ACERVO ---
    elif ops_tab == "Acervo (Exemplares)":
        st.markdown("### 📚 Inventário Local")
        st.caption("Adicione cópias físicas dos livros do Catálogo Geral à sua estante.")
        
        books = pd.read_sql("SELECT id, title, author FROM books ORDER BY title", conn)
        
        with st.form("add_copy"):
            c1, c2 = st.columns([3, 1])
            book_sel = c1.selectbox("Selecione a Obra (Catálogo Geral)", books['title'] + " - " + books['author'])
            code = c2.text_input("Código de Barras/Etiqueta")
            
            if st.form_submit_button("Adicionar Exemplar ao Acervo"):
                # Obter ID do livro
                b_title = book_sel.split(" - ")[0]
                b_id = books[books['title'] == b_title]['id'].values[0]
                
                try:
                    conn.execute("INSERT INTO copies (book_id, library_id, code, status) VALUES (?, ?, ?, 'disponivel')",
                                 (int(b_id), user_lib_id, code))
                    conn.commit()
                    st.success("Exemplar cadastrado!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Erro: Já existe um exemplar com esse código nesta biblioteca.")

        # Listagem
        my_copies = pd.read_sql(f"""
            SELECT c.code, b.title, b.author, c.status 
            FROM copies c JOIN books b ON c.book_id = b.id 
            WHERE c.library_id={user_lib_id}
            ORDER BY c.status, b.title
        """, conn)
        st.dataframe(my_copies, use_container_width=True)

    # --- ABA LEITORES ---
    elif ops_tab == "Leitores":
        st.markdown("### 👥 Cadastro de Leitores")
        st.info("Conformidade LGPD: Dados utilizados apenas para gestão de empréstimos.")
        
        with st.form("new_reader"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Nome Completo")
            doc = c2.text_input("Documento (Opcional)")
            email = c1.text_input("Email ou Telefone")
            lgpd = st.checkbox("Li e aceito os Termos de Privacidade e Uso da Rede", value=False)
            
            if st.form_submit_button("Cadastrar Leitor"):
                if lgpd:
                    try:
                        conn.execute("INSERT INTO users (name, email, document, role, library_id, lgpd_consent, active) VALUES (?, ?, ?, 'leitor', ?, 1, 1)",
                                     (nome, email, doc, user_lib_id))
                        conn.commit()
                        st.success("Leitor cadastrado com sucesso!")
                    except:
                        st.error("Erro: Contato já cadastrado no sistema.")
                else:
                    st.error("O consentimento LGPD é obrigatório para o cadastro.")
        
        readers = pd.read_sql(f"SELECT name, email, active, blocked_until FROM users WHERE role='leitor' AND library_id={user_lib_id}", conn)
        st.dataframe(readers, use_container_width=True)

    # --- ABA RELATÓRIOS ---
    elif ops_tab == "Relatórios":
        st.markdown("### 📈 Indicadores Locais")
        
        # Dados
        loans_hist = pd.read_sql(f"SELECT loan_date, status FROM loans WHERE library_id={user_lib_id}", conn)
        if not loans_hist.empty:
            loans_hist['loan_date'] = pd.to_datetime(loans_hist['loan_date'])
            
            c1, c2 = st.columns(2)
            
            # Gráfico de Empréstimos por mês
            por_mes = loans_hist.groupby(loans_hist['loan_date'].dt.strftime('%Y-%m')).size()
            c1.bar_chart(por_mes)
            c1.caption("Empréstimos por Mês")
            
            # Status
            status_dist = loans_hist['status'].value_counts()
            c2.write("Status dos Empréstimos")
            c2.dataframe(status_dist, use_container_width=True)
            
            st.download_button("Exportar Relatório CSV", loans_hist.to_csv(), "relatorio_emprestimos.csv")
        else:
            st.info("Ainda não há dados suficientes para gerar gráficos.")

    conn.close()

# ==============================================================================
# MAIN APP LOOP
# ==============================================================================

def main():
    login_sidebar()
    
    # Roteamento de Páginas
    if not st.session_state.user:
        page_public_search()
    else:
        role = st.session_state.user['role']
        if role == 'admin_rede':
            page_admin_dashboard()
        elif role == 'coord_local':
            page_library_ops()
        else:
            st.warning("Perfil de leitor não tem acesso ao painel administrativo. Use a busca pública.")

if __name__ == "__main__":
    main()
