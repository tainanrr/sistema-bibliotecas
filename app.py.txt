import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import hashlib
import os

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="BiblioRede Estadual", layout="wide", page_icon="📚")

# --- BANCO DE DADOS ---
DB_FILE = "bibliorede.db"

def get_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    # Criação das tabelas (se não existirem)
    c.execute('''CREATE TABLE IF NOT EXISTS libraries (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, city TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT, role TEXT NOT NULL, library_id INTEGER, active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT NOT NULL, category TEXT, isbn TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS copies (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER, library_id INTEGER, code TEXT NOT NULL, status TEXT DEFAULT 'disponivel')''')
    c.execute('''CREATE TABLE IF NOT EXISTS loans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, copy_id INTEGER, library_id INTEGER, loan_date DATE, due_date DATE, return_date DATE, status TEXT DEFAULT 'aberto')''')
    
    # Admin Padrão
    c.execute("SELECT * FROM users WHERE role='admin_rede'")
    if not c.fetchone():
        pass_hash = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)", ("Admin Rede", "admin@rede.com", pass_hash, "admin_rede"))
        c.execute("INSERT INTO libraries (name, city) VALUES (?, ?)", ("Biblioteca Central", "Capital"))
    conn.commit()
    conn.close()

if not os.path.exists(DB_FILE):
    init_db()
else:
    # Garante que tables existam mesmo se o arquivo existir vazio
    init_db()

# --- FUNÇÕES AUXILIARES ---
def hash_pass(password): return hashlib.sha256(password.encode()).hexdigest()

def login(email, password):
    conn = get_connection()
    user = conn.execute("SELECT id, name, role, library_id FROM users WHERE email=? AND password=?", (email, hash_pass(password))).fetchone()
    conn.close()
    return user

# --- INTERFACE ---
def main():
    if 'user' not in st.session_state: st.session_state.user = None

    with st.sidebar:
        st.title("📚 BiblioRede")
        if st.session_state.user:
            st.write(f"Logado: **{st.session_state.user[1]}**")
            if st.button("Sair"): 
                st.session_state.user = None
                st.rerun()
            st.divider()
            role = st.session_state.user[2]
            if role == 'admin_rede':
                menu = st.radio("Menu", ["Dashboard", "Bibliotecas", "Obras (Catálogo)", "Equipe"])
            elif role == 'coord_local':
                menu = st.radio("Menu", ["Balcão (Circulação)", "Meus Livros", "Leitores"])
            else:
                menu = "Leitor"
        else:
            menu = "Login"

        st.divider()
        st.caption("🔧 Manutenção de Dados")
        # Botão de Download do Banco (Backup)
        with open(DB_FILE, "rb") as f:
            st.download_button("Baixar Backup (.db)", f, file_name="bibliorede_backup.db")

    # TELA PRINCIPAL
    if menu == "Login":
        st.header("Acesso ao Sistema")
        # Busca pública rápida na tela de login
        with st.expander("🔍 Pesquisa Rápida no Acervo (Público)"):
            q = st.text_input("Buscar livro...")
            if q:
                conn = get_connection()
                res = pd.read_sql(f"SELECT b.title, l.name as lib, c.status FROM copies c JOIN books b ON c.book_id=b.id JOIN libraries l ON c.library_id=l.id WHERE b.title LIKE '%{q}%'", conn)
                st.dataframe(res)
                conn.close()
                
        with st.form("login"):
            email = st.text_input("Email")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                u = login(email, senha)
                if u:
                    st.session_state.user = u
                    st.rerun()
                else:
                    st.error("Dados inválidos. (Teste: admin@rede.com / admin123)")

    elif menu == "Dashboard": # Admin
        st.title("Visão Geral da Rede")
        conn = get_connection()
        c1, c2, c3 = st.columns(3)
        c1.metric("Bibliotecas", conn.execute("SELECT count(*) FROM libraries").fetchone()[0])
        c2.metric("Livros (Exemplares)", conn.execute("SELECT count(*) FROM copies").fetchone()[0])
        c3.metric("Empréstimos Ativos", conn.execute("SELECT count(*) FROM loans WHERE status='aberto'").fetchone()[0])
        conn.close()

    elif menu == "Bibliotecas": # Admin
        st.header("Gerenciar Bibliotecas")
        with st.form("add_lib"):
            nome = st.text_input("Nome")
            cidade = st.text_input("Cidade")
            if st.form_submit_button("Criar"):
                conn = get_connection()
                conn.execute("INSERT INTO libraries (name, city) VALUES (?, ?)", (nome, cidade))
                conn.commit()
                conn.close()
                st.success("Criado!")
                st.rerun()
        conn = get_connection()
        st.dataframe(pd.read_sql("SELECT * FROM libraries", conn))
        conn.close()

    elif menu == "Obras (Catálogo)": # Admin
        st.header("Catálogo Bibliográfico Unificado")
        with st.form("add_book"):
            tit = st.text_input("Título")
            aut = st.text_input("Autor")
            if st.form_submit_button("Cadastrar Obra"):
                conn = get_connection()
                conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (tit, aut))
                conn.commit()
                conn.close()
                st.success("Obra cadastrada!")
        
        conn = get_connection()
        st.dataframe(pd.read_sql("SELECT * FROM books ORDER BY id DESC", conn))
        conn.close()
        
    elif menu == "Equipe": # Admin
        st.header("Cadastrar Coordenadores")
        conn = get_connection()
        libs = pd.read_sql("SELECT id, name FROM libraries", conn)
        with st.form("add_staff"):
            nome = st.text_input("Nome")
            mail = st.text_input("Email")
            pwd = st.text_input("Senha", type="password")
            lib_name = st.selectbox("Biblioteca", libs['name'])
            if st.form_submit_button("Salvar"):
                lib_id = libs[libs['name']==lib_name]['id'].values[0]
                conn.execute("INSERT INTO users (name, email, password, role, library_id) VALUES (?, ?, ?, ?, ?)", (nome, mail, hash_pass(pwd), 'coord_local', int(lib_id)))
                conn.commit()
                st.success("Usuário criado!")
        conn.close()

    elif menu == "Meus Livros": # Coord
        st.header("Acervo Local (Exemplares)")
        lib_id = st.session_state.user[3]
        conn = get_connection()
        obras = pd.read_sql("SELECT id, title FROM books", conn)
        
        with st.form("add_copy"):
            obra = st.selectbox("Obra", obras['title'])
            codigo = st.text_input("Código/Etiqueta")
            if st.form_submit_button("Adicionar Exemplar"):
                book_id = obras[obras['title']==obra]['id'].values[0]
                conn.execute("INSERT INTO copies (book_id, library_id, code) VALUES (?, ?, ?)", (int(book_id), lib_id, codigo))
                conn.commit()
                st.success("Exemplar adicionado!")
                st.rerun()
        
        meus = pd.read_sql(f"SELECT c.code, b.title, c.status FROM copies c JOIN books b ON c.book_id=b.id WHERE c.library_id={lib_id}", conn)
        st.dataframe(meus)
        conn.close()

    elif menu == "Leitores": # Coord
        st.header("Cadastro de Leitores")
        lib_id = st.session_state.user[3]
        with st.form("add_leitor"):
            n = st.text_input("Nome")
            e = st.text_input("Email/Tel")
            if st.form_submit_button("Cadastrar"):
                conn = get_connection()
                try:
                    conn.execute("INSERT INTO users (name, email, role, library_id) VALUES (?, ?, 'leitor', ?)", (n, e, lib_id))
                    conn.commit()
                    st.success("Feito!")
                except: st.error("Email já existe.")
                conn.close()

    elif menu == "Balcão (Circulação)": # Coord
        st.header("Empréstimos e Devoluções")
        lib_id = st.session_state.user[3]
        conn = get_connection()
        
        tab1, tab2 = st.tabs(["Empréstimo", "Devolução"])
        with tab1:
            users = pd.read_sql(f"SELECT id, name FROM users WHERE role='leitor' AND library_id={lib_id}", conn)
            copies = pd.read_sql(f"SELECT c.id, b.title, c.code FROM copies c JOIN books b ON c.book_id=b.id WHERE c.library_id={lib_id} AND c.status='disponivel'", conn)
            
            if not users.empty and not copies.empty:
                u_sel = st.selectbox("Leitor", users['name'])
                c_sel = st.selectbox("Livro", copies['title'] + " | " + copies['code'])
                if st.button("Confirmar Saída"):
                    uid = users[users['name']==u_sel]['id'].values[0]
                    cod = c_sel.split(" | ")[1]
                    cid = copies[copies['code']==cod]['id'].values[0]
                    d_hoje = datetime.now()
                    d_fim = d_hoje + timedelta(days=14)
                    conn.execute("INSERT INTO loans (user_id, copy_id, library_id, loan_date, due_date) VALUES (?, ?, ?, ?, ?)", (int(uid), int(cid), lib_id, d_hoje, d_fim))
                    conn.execute(f"UPDATE copies SET status='emprestado' WHERE id={cid}")
                    conn.commit()
                    st.success("Empréstimo realizado!")
                    st.rerun()
            else: st.warning("Cadastre leitores e exemplares primeiro.")

        with tab2:
            loans = pd.read_sql(f"SELECT l.id, u.name, b.title FROM loans l JOIN users u ON l.user_id=u.id JOIN copies c ON l.copy_id=c.id JOIN books b ON c.book_id=b.id WHERE l.library_id={lib_id} AND l.status='aberto'", conn)
            if not loans.empty:
                l_sel = st.selectbox("Devolução de:", loans['name'] + " - " + loans['title'])
                if st.button("Confirmar Devolução"):
                    lid = loans[loans['name'] + " - " + loans['title'] == l_sel]['id'].values[0]
                    # Pegar copy_id
                    cid = conn.execute(f"SELECT copy_id FROM loans WHERE id={lid}").fetchone()[0]
                    conn.execute(f"UPDATE loans SET status='devolvido', return_date='{datetime.now()}' WHERE id={lid}")
                    conn.execute(f"UPDATE copies SET status='disponivel' WHERE id={cid}")
                    conn.commit()
                    st.success("Devolvido!")
                    st.rerun()
        conn.close()

if __name__ == "__main__":
    main()