import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

// Mapeamento: nome/ID do produto HeroSpark → slug do plano
const PLAN_MAP: Record<string, string> = {
  'basico':         'basico',
  'básico':         'basico',
  'plano basico':   'basico',
  'plano básico':   'basico',
  'intermediario':  'intermediario',
  'intermediário':  'intermediario',
  'plano intermediario': 'intermediario',
  'plano intermediário': 'intermediario',
  'premium':        'premium',
  'plano premium':  'premium',
  'premium plus':   'premium-plus',
  'premium-plus':   'premium-plus',
  'plano premium plus': 'premium-plus',
};

function resolvePlano(raw: string): string {
  const key = raw.toLowerCase().trim();
  return PLAN_MAP[key] ?? 'basico';
}

Deno.serve(async (req) => {
  // Validar segredo do webhook
  const secret = Deno.env.get('WEBHOOK_SECRET');
  if (secret) {
    const token = req.headers.get('x-webhook-secret') ?? req.headers.get('authorization')?.replace('Bearer ', '');
    if (token !== secret) {
      return new Response('Unauthorized', { status: 401 });
    }
  }

  if (req.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405 });
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return new Response('Bad Request', { status: 400 });
  }

  // HeroSpark envia o payload em diferentes formatos dependendo do evento.
  // Suportamos o formato padrão de purchase.completed:
  // { event, data: { customer: { email, name }, product: { name } } }
  const event = (body.event as string) ?? '';
  if (!event.includes('purchase') && !event.includes('sale') && !event.includes('order')) {
    // Ignora eventos que não sejam de compra
    return new Response(JSON.stringify({ ignored: true }), { status: 200 });
  }

  const data = (body.data ?? body) as Record<string, unknown>;
  const customer = (data.customer ?? data.buyer ?? {}) as Record<string, string>;
  const product  = (data.product  ?? data.offer ?? data.plan ?? {}) as Record<string, string>;

  const email    = customer.email ?? (data.email as string);
  const name     = customer.name  ?? customer.full_name ?? '';
  const planName = product.name   ?? (data.product_name as string) ?? (data.plan as string) ?? 'basico';

  if (!email) {
    return new Response(JSON.stringify({ error: 'email não encontrado no payload' }), { status: 422 });
  }

  const plano = resolvePlano(planName);

  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
  );

  // Busca se o usuário já existe
  const { data: { users } } = await supabase.auth.admin.listUsers();
  const existing = users.find(u => u.email === email);

  let userId: string;

  if (existing) {
    userId = existing.id;
    // Atualiza metadados com o novo plano
    await supabase.auth.admin.updateUserById(userId, {
      user_metadata: { plano, name },
    });
  } else {
    // Cria o usuário e envia magic link de boas-vindas
    const { data: created, error } = await supabase.auth.admin.createUser({
      email,
      email_confirm: true,
      user_metadata: { plano, name },
    });
    if (error || !created.user) {
      return new Response(JSON.stringify({ error: error?.message }), { status: 500 });
    }
    userId = created.user.id;
  }

  // Upsert na tabela profiles
  const { error: profileError } = await supabase
    .from('profiles')
    .upsert({ id: userId, email, plano }, { onConflict: 'id' });

  if (profileError) {
    return new Response(JSON.stringify({ error: profileError.message }), { status: 500 });
  }

  // Envia magic link para o cliente acessar a área de membros
  const siteUrl = Deno.env.get('SITE_URL') ?? 'https://guianttunes25-debug.github.io/motorista-pro';
  await supabase.auth.admin.generateLink({
    type: 'magiclink',
    email,
    options: { redirectTo: `${siteUrl}/membros.html` },
  });

  // Envia o e-mail de boas-vindas com o link
  await supabase.auth.admin.inviteUserByEmail(email, {
    redirectTo: `${siteUrl}/membros.html`,
    data: { plano, name },
  });

  return new Response(JSON.stringify({ ok: true, plano, userId }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
});
