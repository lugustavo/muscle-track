#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para popular o banco de dados com dados iniciais

  python populate_db.py          # só grupos musculares, exercícios e modelos de treino
  python populate_db.py --demo   # + um estúdio de exemplo: pacotes, 18 alunos, aulas, pagamentos, treinos
                                 #   (NÃO use em produção; veja core/demo_data.py para a lista dos casos)

Nunca cria contas com senha fixa: a senha da demo vem de DEMO_PASSWORD ou é gerada aleatoriamente e exibida uma única
vez. Se ainda não existir nenhuma personal/admin, a demo cria `personal@example.com`; do contrário, entre com a sua conta.
O administrador/personal de verdade é criado com `python manage.py createsuperuser`.
"""
import os
import sys

import django

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core import demo_data
from core.models import Exercise, MuscleGroup, Routine


def main():
    demo = '--demo' in sys.argv

    print("=" * 60)
    print("POPULANDO BANCO DE DADOS - MUSCLE TRACK")
    print("=" * 60)

    demo_data.load_library(demo_data.Demo())
    print(f"✓ {MuscleGroup.objects.count()} grupos musculares, {Exercise.objects.count()} exercícios, "
          f"{Routine.objects.count()} modelos de treino")

    if demo:
        print("\nCriando o estúdio de exemplo (alguns segundos)...")
        summary, password = demo_data.populate(os.environ.get('DEMO_PASSWORD'))
        if summary is None:
            print("A demonstração já estava carregada: nada foi alterado.")
        else:
            print("✓ " + ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in summary.items()
                                   if type(count) is int))
            if summary['trainer_created']:
                print("\nContas (todas com a mesma senha):")
                print(f"  personal: {summary['trainer']}")
            else:
                print(f"\nContas (a personal é a sua conta existente, {summary['trainer']}, com a sua senha):")
            print("  alunos:   ana.ferreira@example.com, carla.mendes@example.com, olivia.torres@example.com, ...")
            print("            (Bruno e Tiago não têm app; Diego tem convite pendente: ainda não tem senha)")
            print(f"  senha:    {password}   <- anote, não será exibida de novo")

    print("\n" + "=" * 60)
    print("DADOS INICIAIS CRIADOS COM SUCESSO!")
    print("=" * 60)
    if not demo:
        print("\nCrie a personal/admin com: python manage.py createsuperuser")
    print("=" * 60)


if __name__ == '__main__':
    main()
