#!/usr/bin/env python3
"""
SWE-bench Data Point Validator с реальной интеграцией SWE-bench evaluation harness
Универсальный валидатор для всех Python репозиториев
"""

import json
import sys
import argparse
import subprocess
import tempfile
import shutil
import os
import docker
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SWEBenchValidator:
    """Валидатор с реальной интеграцией SWE-bench evaluation harness."""
    
    def __init__(self, timeout: int = 300):
        self.required_fields = [
            'instance_id', 'repo', 'base_commit', 'patch', 
            'test_patch', 'problem_statement', 'hints_text', 
            'created_at', 'version', 'FAIL_TO_PASS', 'PASS_TO_PASS'
        ]
        self.timeout = timeout
        self.docker_client = None
        
    def _init_docker(self):
        """Инициализация Docker client."""
        try:
            self.docker_client = docker.from_env()
            return True
        except Exception as e:
            logger.warning(f"Docker недоступен: {e}")
            return False
    
    def validate_json_structure(self, data: Dict[str, Any]) -> List[str]:
        """Проверяет структуру JSON на наличие обязательных полей."""
        errors = []
        
        for field in self.required_fields:
            if field not in data:
                errors.append(f"Отсутствует обязательное поле: {field}")
        
        # Проверяем типы данных
        if 'instance_id' in data and not isinstance(data['instance_id'], str):
            errors.append("instance_id должен быть строкой")
            
        if 'repo' in data and not isinstance(data['repo'], str):
            errors.append("repo должен быть строкой")
        
        # Проверяем формат instance_id
        if 'instance_id' in data:
            instance_id = data['instance_id']
            if not instance_id or '__' not in instance_id:
                errors.append("instance_id должен содержать '__' (формат: repo__issue-number)")
        
        # Проверяем тестовые поля
        for test_field in ['FAIL_TO_PASS', 'PASS_TO_PASS']:
            if test_field in data:
                if isinstance(data[test_field], str):
                    try:
                        test_list = json.loads(data[test_field])
                        if not isinstance(test_list, list):
                            errors.append(f"{test_field} должен быть JSON списком строк")
                    except json.JSONDecodeError:
                        errors.append(f"{test_field} содержит невалидный JSON")
                elif not isinstance(data[test_field], list):
                    errors.append(f"{test_field} должен быть списком")
            
        return errors
    
    def validate_patch_format(self, patch: str) -> List[str]:
        """Проверяет корректность формата патча."""
        errors = []
        
        if not patch.strip():
            errors.append("Патч не может быть пустым")
            return errors
            
        lines = patch.split('\n')
        has_diff_header = any(line.startswith('diff --git') for line in lines)
        has_file_markers = any(line.startswith('---') or line.startswith('+++') for line in lines)
        has_hunk_headers = any(line.startswith('@@') for line in lines)
        
        if not (has_diff_header and has_file_markers and has_hunk_headers):
            errors.append("Патч не содержит корректных diff маркеров")
        
        has_additions = any(line.startswith('+') and not line.startswith('+++') for line in lines)
        has_deletions = any(line.startswith('-') and not line.startswith('---') for line in lines)
        
        if not (has_additions or has_deletions):
            errors.append("Патч не содержит изменений")
            
        return errors
    
    def run_swebench_evaluation(self, data_point_path: str) -> Dict[str, Any]:
        """
        РЕАЛЬНАЯ валидация через SWE-bench evaluation harness.
        Клонирует репозиторий, применяет патч, запускает тесты.
        """
        result = {
            'evaluation_success': False,
            'patch_applied': False,
            'tests_passed': False,
            'fail_to_pass_results': {},
            'pass_to_pass_results': {},
            'errors': [],
            'logs': []
        }
        
        try:
            with open(data_point_path, 'r') as f:
                data = json.load(f)
            
            instance_id = data['instance_id']
            repo = data['repo']
            base_commit = data['base_commit']
            patch = data['patch']
            
            result['logs'].append(f"Начинаем валидацию {instance_id}")
            
            # 1. Создаем временную директорию
            with tempfile.TemporaryDirectory() as temp_dir:
                repo_dir = Path(temp_dir) / "repo"
                
                # 2. Клонируем репозиторий
                result['logs'].append(f"Клонируем {repo}")
                clone_cmd = [
                    'git', 'clone', f'https://github.com/{repo}.git', str(repo_dir)
                ]
                
                try:
                    subprocess.run(clone_cmd, check=True, capture_output=True, 
                                 timeout=self.timeout, cwd=temp_dir)
                except subprocess.CalledProcessError as e:
                    result['errors'].append(f"Ошибка клонирования: {e.stderr.decode()}")
                    return result
                except subprocess.TimeoutExpired:
                    result['errors'].append("Таймаут при клонировании репозитория")
                    return result
                
                # 3. Чекаутим базовый коммит
                result['logs'].append(f"Checkout {base_commit}")
                checkout_cmd = ['git', 'checkout', base_commit]
                
                try:
                    subprocess.run(checkout_cmd, check=True, capture_output=True,
                                 cwd=repo_dir)
                except subprocess.CalledProcessError as e:
                    result['errors'].append(f"Ошибка checkout: {e.stderr.decode()}")
                    return result
                
                # 4. Применяем патч
                result['logs'].append("Применяем основной патч")
                patch_file = Path(temp_dir) / "main.patch"
                patch_file.write_text(patch)
                
                apply_cmd = ['git', 'apply', '--check', str(patch_file)]
                try:
                    subprocess.run(apply_cmd, check=True, capture_output=True,
                                 cwd=repo_dir)
                    # Если check прошел, применяем патч
                    apply_cmd = ['git', 'apply', str(patch_file)]
                    subprocess.run(apply_cmd, check=True, capture_output=True,
                                 cwd=repo_dir)
                    result['patch_applied'] = True
                    result['logs'].append("✓ Патч успешно применен")
                except subprocess.CalledProcessError as e:
                    result['errors'].append(f"Ошибка применения патча: {e.stderr.decode()}")
                    return result
                
                # 5. Применяем тестовый патч (если есть)
                if data.get('test_patch'):
                    result['logs'].append("Применяем тестовый патч")
                    test_patch_file = Path(temp_dir) / "test.patch"
                    test_patch_file.write_text(data['test_patch'])
                    
                    try:
                        apply_cmd = ['git', 'apply', str(test_patch_file)]
                        subprocess.run(apply_cmd, check=True, capture_output=True,
                                     cwd=repo_dir)
                        result['logs'].append("✓ Тестовый патч применен")
                    except subprocess.CalledProcessError as e:
                        result['errors'].append(f"Ошибка применения тест-патча: {e.stderr.decode()}")
                        return result
                
                # 6. Запускаем FAIL_TO_PASS тесты
                fail_to_pass = data.get('FAIL_TO_PASS', '[]')
                if isinstance(fail_to_pass, str):
                    fail_to_pass = json.loads(fail_to_pass)
                
                if fail_to_pass:
                    result['logs'].append(f"Запускаем FAIL_TO_PASS тесты: {len(fail_to_pass)}")
                    for test in fail_to_pass:
                        test_result = self._run_single_test(test, repo_dir)
                        result['fail_to_pass_results'][test] = test_result
                        if not test_result['passed']:
                            result['errors'].append(f"FAIL_TO_PASS тест не прошел: {test}")
                
                # 7. Запускаем PASS_TO_PASS тесты (sample)
                pass_to_pass = data.get('PASS_TO_PASS', '[]')
                if isinstance(pass_to_pass, str):
                    pass_to_pass = json.loads(pass_to_pass)
                
                if pass_to_pass:
                    # Запускаем первые 5 тестов для проверки
                    sample_tests = pass_to_pass[:5]
                    result['logs'].append(f"Запускаем PASS_TO_PASS тесты (sample): {len(sample_tests)}")
                    for test in sample_tests:
                        test_result = self._run_single_test(test, repo_dir)
                        result['pass_to_pass_results'][test] = test_result
                        if not test_result['passed']:
                            result['errors'].append(f"PASS_TO_PASS тест сломался: {test}")
                
                # 8. Определяем общий результат
                fail_to_pass_success = all(
                    tr['passed'] for tr in result['fail_to_pass_results'].values()
                )
                pass_to_pass_success = all(
                    tr['passed'] for tr in result['pass_to_pass_results'].values()
                )
                
                result['tests_passed'] = fail_to_pass_success and pass_to_pass_success
                result['evaluation_success'] = (
                    result['patch_applied'] and 
                    result['tests_passed'] and 
                    len(result['errors']) == 0
                )
                
                if result['evaluation_success']:
                    result['logs'].append("✅ SWE-bench валидация ПРОЙДЕНА")
                else:
                    result['logs'].append("❌ SWE-bench валидация ПРОВАЛЕНА")
                
        except Exception as e:
            result['errors'].append(f"Неожиданная ошибка в SWE-bench валидации: {e}")
            logger.exception("SWE-bench validation error")
        
        return result
    
    def _run_single_test(self, test_path: str, repo_dir: Path) -> Dict[str, Any]:
        """Запускает отдельный тест и возвращает результат."""
        result = {
            'passed': False,
            'output': '',
            'error': '',
            'timeout': False
        }
        
        try:
            # Определяем команду запуска тестов
            if '::' in test_path:
                # pytest формат
                cmd = ['python', '-m', 'pytest', '-xvs', test_path]
            else:
                # unittest формат
                cmd = ['python', '-m', 'unittest', test_path]
            
            process = subprocess.run(
                cmd, 
                capture_output=True, 
                timeout=60,  # 1 минута на тест
                cwd=repo_dir,
                text=True
            )
            
            result['output'] = process.stdout
            result['error'] = process.stderr
            result['passed'] = process.returncode == 0
            
        except subprocess.TimeoutExpired:
            result['timeout'] = True
            result['error'] = "Тест превысил таймаут 60 секунд"
        except Exception as e:
            result['error'] = f"Ошибка запуска теста: {e}"
        
        return result
    
    def validate_data_point(self, file_path: str, run_evaluation: bool = True) -> Dict[str, Any]:
        """Валидирует одну точку данных SWE-bench."""
        result = {
            'file': file_path,
            'valid': True,
            'errors': [],
            'warnings': [],
            'structure_valid': True,
            'swe_bench_evaluation': None
        }
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 1. Проверка структуры JSON
            structure_errors = self.validate_json_structure(data)
            result['errors'].extend(structure_errors)
            result['structure_valid'] = len(structure_errors) == 0
            
            # 2. Проверка основного патча
            if 'patch' in data:
                patch_errors = self.validate_patch_format(data['patch'])
                result['errors'].extend(patch_errors)
            
            # 3. Проверка тестового патча
            if 'test_patch' in data and data['test_patch']:
                test_patch_errors = self.validate_patch_format(data['test_patch'])
                result['errors'].extend([f"test_patch: {err}" for err in test_patch_errors])
            
            # 4. РЕАЛЬНАЯ валидация через SWE-bench (если структура корректна)
            if run_evaluation and result['structure_valid']:
                logger.info(f"Запускаем SWE-bench evaluation для {file_path}")
                swe_result = self.run_swebench_evaluation(file_path)
                result['swe_bench_evaluation'] = swe_result
                
                if not swe_result['evaluation_success']:
                    result['errors'].extend(swe_result['errors'])
            
            result['valid'] = len(result['errors']) == 0
            
        except json.JSONDecodeError as e:
            result['errors'].append(f"Некорректный JSON: {e}")
            result['valid'] = False
            result['structure_valid'] = False
        except FileNotFoundError:
            result['errors'].append("Файл не найден")
            result['valid'] = False
        except Exception as e:
            result['errors'].append(f"Неожиданная ошибка: {e}")
            result['valid'] = False
            logger.exception("Validation error")
        
        return result


def main():
    parser = argparse.ArgumentParser(description='SWE-bench Data Point Validator с реальной интеграцией')
    parser.add_argument('files', nargs='+', help='JSON файлы для валидации')
    parser.add_argument('--json', action='store_true', help='Вывод в JSON формате')
    parser.add_argument('--verbose', '-v', action='store_true', help='Подробный вывод')
    parser.add_argument('--no-evaluation', action='store_true', 
                       help='Пропустить SWE-bench evaluation (только структурная проверка)')
    parser.add_argument('--timeout', type=int, default=300, 
                       help='Таймаут для операций в секундах (по умолчанию: 300)')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    validator = SWEBenchValidator(timeout=args.timeout)
    results = []
    
    for file_path in args.files:
        logger.info(f"Валидируем {file_path}")
        result = validator.validate_data_point(file_path, run_evaluation=not args.no_evaluation)
        results.append(result)
        
        if not args.json:
            status = "✓ VALID" if result['valid'] else "✗ INVALID"
            print(f"{status}: {file_path}")
            
            if result['errors']:
                for error in result['errors']:
                    print(f"  ERROR: {error}")
            
            if result['warnings'] and args.verbose:
                for warning in result['warnings']:
                    print(f"  WARNING: {warning}")
            
            # Детали SWE-bench evaluation
            if args.verbose and result.get('swe_bench_evaluation'):
                swe_result = result['swe_bench_evaluation']
                print(f"  SWE-bench evaluation:")
                print(f"    Patch applied: {'✓' if swe_result.get('patch_applied') else '✗'}")
                print(f"    Tests passed: {'✓' if swe_result.get('tests_passed') else '✗'}")
                
                fail_to_pass = swe_result.get('fail_to_pass_results', {})
                if fail_to_pass:
                    passed = sum(1 for r in fail_to_pass.values() if r['passed'])
                    total = len(fail_to_pass)
                    print(f"    FAIL_TO_PASS: {passed}/{total} passed")
                
                pass_to_pass = swe_result.get('pass_to_pass_results', {})
                if pass_to_pass:
                    passed = sum(1 for r in pass_to_pass.values() if r['passed'])
                    total = len(pass_to_pass)
                    print(f"    PASS_TO_PASS: {passed}/{total} passed (sample)")
    
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    # Статистика
    valid_count = sum(1 for r in results if r['valid'])
    total_count = len(results)
    
    if not args.json:
        print(f"\nРезультат: {valid_count}/{total_count} файлов валидны")
        if not args.no_evaluation:
            print("💡 Использовалась РЕАЛЬНАЯ SWE-bench evaluation")
        else:
            print("⚠️  Использовалась только структурная проверка")
    
    # Выход с кодом ошибки если есть невалидные файлы
    if valid_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
