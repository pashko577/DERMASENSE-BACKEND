# Cómo se conectan Frontend, Backend y Supabase

> Documento explicativo. No es código todavía — es el mapa antes de construir.

---

## 1. La idea en una frase

**Hay un solo proyecto de Supabase.** Los dos repositorios (`DERMASENSE` y
`DERMASENSE-BACKEND`) le hablan a esa misma base de datos, pero cada uno con una llave
distinta y para cosas distintas. El backend nunca inventa su propia sesión: **usa la
sesión que el frontend ya creó.**

```
┌────────────┐        ①        ┌──────────────┐        ②        ┌────────────┐
│  Supabase  │ ◄──────────────► │   Frontend   │                 │  Backend   │
│ (Postgres  │   login, cookies │  (Next.js)   │                 │ (FastAPI)  │
│  + Auth)   │                  └──────┬───────┘                 └─────▲──────┘
│            │                         │                               │
│            │            ③ el frontend le pasa al backend             │
│            │               el "carnet" (JWT) del usuario ────────────┘
│            │                                                         │
│            │ ◄───────────────────────────────────────────────────────┘
└────────────┘        ④ el backend usa ese carnet para hablarle
                          a Supabase como si fuera el usuario
```

Léelo así: el usuario **inicia sesión una sola vez**, en el frontend. Esa sesión genera un
"carnet" (el JWT). El frontend, cuando necesita algo del backend, le muestra ese carnet.
El backend lo revisa, y si es válido, lo usa para hablarle a Supabase **como si fuera esa
misma persona** — nunca con una llave maestra.

---

## 2. Conexión ① — Supabase ↔ Frontend

**Esto ya casi existe.** El `package.json` del frontend ya tiene `@supabase/supabase-js` y
`@supabase/ssr` instalados, y el `.env.example` ya tiene las dos variables públicas
listas. Solo falta:

1. Crear el proyecto real en [supabase.com](https://supabase.com) (gratis).
2. Copiar dos valores del panel de Supabase a `.env.local`:
   - `NEXT_PUBLIC_SUPABASE_URL` — la URL del proyecto.
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY` — la llave pública. Es segura de exponer porque
     Row Level Security (RLS) decide fila por fila qué puede ver cada usuario.
3. Correr las migraciones de [`BACKEND_SCHEMA.md`](../DERMASENSE/docs/BACKEND_SCHEMA.md)
   para crear las tablas.

Con eso, el frontend ya puede:

- Mostrar login/registro (Supabase Auth se encarga de contraseñas, tokens, recuperación).
- Guardar y leer `simulations` directamente, protegido por RLS.

**Lo que nunca debe pasar aquí:** la otra llave que existe en Supabase,
`SUPABASE_SERVICE_ROLE_KEY`, es una llave maestra que **se salta RLS por completo**. Solo
se usa una vez, a mano, para correr las migraciones — jamás dentro de una página o una
API que atienda usuarios.

---

## 3. Conexión ② — Supabase ↔ Backend

El backend habla con la **misma** base de datos, pero con una diferencia importante: **no
tiene páginas de login.** No las necesita — la sesión ya existe, la creó el frontend.

Lo que el backend sí necesita son tres valores (los mismos del proyecto, más uno nuevo):

| Variable | Qué es | Para qué |
|---|---|---|
| `SUPABASE_URL` | La misma URL de arriba | Saber a qué proyecto conectarse |
| `SUPABASE_ANON_KEY` | La misma llave pública | Consultar la base como "un usuario", nunca como admin |
| `SUPABASE_JWT_SECRET` | Un secreto nuevo, del panel de Supabase | Comprobar que el "carnet" que le llega es legítimo y no fue falsificado |

El backend recibe el carnet (JWT), verifica con `SUPABASE_JWT_SECRET` que de verdad lo
emitió Supabase y no está vencido, y luego arma una consulta a Postgres **usando ese mismo
carnet** en vez de una llave propia. Postgres, al ver ese carnet, aplica las mismas reglas
de RLS que aplicaría si la consulta viniera del frontend. El backend nunca ve más datos de
los que el usuario dueño del carnet podría ver.

---

## 4. Conexión ③④ — Frontend ↔ Backend

Esta es la conexión que falta por completo hoy — no hay nada construido de ningún lado.

**El flujo, en 4 pasos:**

1. El usuario ya inició sesión en el frontend (conexión ①). Next.js tiene su carnet
   (JWT) guardado en una cookie.
2. El usuario pide algo que solo el backend puede hacer — por ejemplo, "genera el reporte
   con IA" o "descarga el Excel".
3. El frontend hace una petición HTTP al backend (`fetch`) y **adjunta el carnet** en un
   encabezado: `Authorization: Bearer <carnet>`.
4. El backend recibe la petición, valida el carnet (conexión ②), y responde.

```
Usuario hace clic en "Generar reporte"
        │
        ▼
Next.js toma el JWT de la sesión actual
        │
        ▼
fetch("https://api-dermasense.onrender.com/api/v1/reports/123",
      { headers: { Authorization: "Bearer " + jwt } })
        │
        ▼
FastAPI verifica el JWT contra SUPABASE_JWT_SECRET
        │
        ▼
FastAPI consulta Supabase con ese JWT (RLS activo)
        │
        ▼
FastAPI llama a Claude, arma el reporte
        │
        ▼
Responde al frontend → se muestra al usuario
```

**Dos cosas que hay que configurar para que esto funcione, y que no son opcionales:**

- **CORS en el backend.** Por defecto, un navegador bloquea que una página en
  `dermasense.vercel.app` le hable a un servidor en `dermasense-backend.onrender.com` —
  son "orígenes" distintos. El backend debe declarar explícitamente que confía en el
  dominio del frontend (variable `CORS_ORIGINS`).
- **Una variable nueva en el frontend**, `NEXT_PUBLIC_BACKEND_URL` (o similar), con la
  dirección del backend desplegado, para que el `fetch` sepa a dónde ir.

---

## 5. Resumen de variables por lado

No son las mismas variables en los dos repos — cada uno solo tiene las que necesita.

**Frontend (`DERMASENSE/.env.local`):**
```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...        # solo para migraciones, nunca en runtime
NEXT_PUBLIC_BACKEND_URL=...          # nueva — dirección del backend
```

**Backend (`DERMASENSE-BACKEND/.env`):**
```
SUPABASE_URL=...                     # mismo proyecto, mismo valor que arriba
SUPABASE_ANON_KEY=...                # misma llave pública
SUPABASE_JWT_SECRET=...              # nuevo — para verificar el carnet
ANTHROPIC_API_KEY=...
CORS_ORIGINS=https://dermasense.vercel.app,http://localhost:3000
```

`SUPABASE_URL` y `SUPABASE_ANON_KEY` son **el mismo valor copiado en los dos archivos** —
por eso es un solo proyecto de Supabase, no dos.

---

## 6. Por qué así y no de otra forma

- **Una sola vez que el usuario inicia sesión**, no dos. Si el backend tuviera su propio
  login, el usuario tendría dos contraseñas y dos sesiones que sincronizar — innecesario.
- **Ninguna llave maestra viaja por la red.** El backend nunca tiene `service_role`; si
  alguien lo comprometiera, solo podría ver lo que el usuario cuyo carnet estaba usando
  en ese momento podría ver.
- **Las reglas de seguridad viven en un solo lugar** (RLS en Postgres, ver
  [ADR-003](../DERMASENSE/docs/adr/003-supabase-rls.md)), no repetidas en dos códigos que
  podrían desincronizarse.

---

## 7. Próximo paso (cuando se autorice construir)

En este orden, porque cada uno depende del anterior:

1. Crear el proyecto de Supabase y correr las migraciones.
2. Conectar el frontend a Supabase (conexión ①) — login funcionando.
3. Conectar el backend a Supabase (conexión ②) — verificación de carnet funcionando.
4. Conectar frontend → backend (conexión ③④) — CORS + el primer `fetch` real.
