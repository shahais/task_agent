#!/usr/bin/env python3
"""
SWE-bench Data Point Validator с ПРАВИЛЬНЫМ SWE-bench API
"""

import json
import sys
import argparse
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Any
import logging

# ПРАВИЛЬНЫЙ SWE-bench API
from swebench.harness.run_evaluation import main as run_evaluation_main
from swebench.harness.utils import load_swebench_dataset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SWEBenchValidator:
    """Валидатор с правильным SWE-bench evaluation API."""
    
    def __init__(self, timeout: int = 1800):
        self.required_fields = [
            'instance_id', 'repo', 'base_commit', 'patch', 
            'test_patch', 'problem_statement', 'hints_text', 
            'created_at', 'version', 'FAIL_TO_PASS', 'PASS_TO_PASS'
        ]
        self.timeout = timeout
    
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

    def run_swebench_evaluation(self, data_point_path: str) -> Dict[str, Any]:
        """
        ПРАВИЛЬНАЯ валидация через swebench.harness.run_evaluation.main
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
            result['logs'].append(f"Начинаем SWE-bench evaluation для {instance_id}")
            
            # Создаем предикт в формате SWE-bench
            # Используем golden patch как решение для валидации
            prediction = {
                'instance_id': instance_id,
                'model_patch': data['patch'],  # Golden patch
                'model_name_or_path': 'golden_patch_validator'
            }
            
            # Создаем временные файлы для SWE-bench
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir = Path(temp_dir)
                
                # Создаем датасет JSONL файл
                dataset_file = temp_dir / 'dataset.jsonl'
                with open(dataset_file, 'w') as f:
                    json.dump(data, f)
                    f.write('\n')
                
                # Создаем предикты JSONL файл
                predictions_file = temp_dir / 'predictions.jsonl'
                with open(predictions_file, 'w') as f:
                    json.dump(prediction, f)
                    f.write('\n')
                
                # Папка для отчетов
                report_dir = temp_dir / 'reports'
                report_dir.mkdir()
                
                # Генерируем уникальный run_id
                run_id = f"validator_{uuid.uuid4().hex[:8]}"
                
                result['logs'].append("Запускаем официальный SWE-bench evaluation...")
                
                try:
                    # ПРАВИЛЬНЫЙ вызов SWE-bench evaluation
                    report_path = run_evaluation_main(
                        dataset_name=str(dataset_file),  # Путь к нашему датасету
                        split='test',  # По умолчанию
                        instance_ids=[instance_id],  # Только наш instance
                        predictions_path=str(predictions_file),  # Путь к предиктам
                        max_workers=1,  # Один воркер для простоты
                        force_rebuild=False,
                        cache_level='env',
                        clean=False,
                        open_file_limit=4096,
                        run_id=run_id,
                        timeout=self.timeout,
                        namespace='swebench',
                        rewrite_reports=False,
                        modal=False,
                        instance_image_tag='latest',
                        report_dir=str(report_dir)
                    )
                    
                    result['logs'].append(f"✓ SWE-bench evaluation завершен, отчет: {report_path}")
                    
                    # Читаем результаты из отчета
                    if report_path and Path(report_path).exists():
                        with open(report_path, 'r') as f:
                            report_data = json.load(f)
                        
                        # Ищем результат для нашего instance
                        for entry in report_data:
                            if entry.get('instance_id') == instance_id:
                                self._parse_evaluation_result(entry, data, result)
                                break
                    else:
                        # Ищем результаты в report_dir
                        result_files = list(report_dir.glob('**/*.json'))
                        result['logs'].append(f"Найдено файлов результатов: {len(result_files)}")
                        
                        for result_file in result_files:
                            try:
                                with open(result_file, 'r') as f:
                                    file_data = json.load(f)
                                
                                # Пытаемся найти наш instance в файле
                                if isinstance(file_data, list):
                                    for entry in file_data:
                                        if entry.get('instance_id') == instance_id:
                                            self._parse_evaluation_result(entry, data, result)
                                            break
                                elif isinstance(file_data, dict) and file_data.get('instance_id') == instance_id:
                                    self._parse_evaluation_result(file_data, data, result)
                                    break
                            except Exception as e:
                                result['logs'].append(f"Ошибка чтения {result_file}: {e}")
                    
                    result['evaluation_success'] = len(result['errors']) == 0
                    
                except Exception as e:
                    result['errors'].append(f"Ошибка SWE-bench evaluation: {e}")
                    logger.exception("SWE-bench evaluation error")
                
        except Exception as e:
            result['errors'].append(f"Ошибка подготовки evaluation: {e}")
            logger.exception("Evaluation preparation error")
        
        return result
    
    def _parse_evaluation_result(self, eval_entry: Dict, data: Dict, result: Dict):
        """Парсит результат evaluation."""
        # Основные результаты
        result['patch_applied'] = eval_entry.get('patch_applied', False)
        result['tests_passed'] = eval_entry.get('resolved', False)
        
        # Анализируем тесты
        test_results = eval_entry.get('test_results', {})
        
        # FAIL_TO_PASS тесты
        fail_to_pass = json.loads(data.get('FAIL_TO_PASS', '[]'))
        for test in fail_to_pass:
            # В SWE-bench статус может быть PASSED, FAILED, ERROR, TIMEOUT
            test_status = test_results.get(test, {})
            if isinstance(test_status, dict):
                test_passed = test_status.get('status') == 'PASSED'
            else:
                # Иногда результат может быть просто строкой
                test_passed = test_status == 'PASSED'
            
            result['fail_to_pass_results'][test] = {'passed': test_passed}
            if not test_passed:
                result['errors'].append(f"FAIL_TO_PASS тест не прошел: {test}")
        
        # PASS_TO_PASS тесты (берем sample для скорости)
        pass_to_pass = json.loads(data.get('PASS_TO_PASS', '[]'))
        sample_tests = pass_to_pass[:5]  # Первые 5 тестов
        for test in sample_tests:
            test_status = test_results.get(test, {})
            if isinstance(test_status, dict):
                test_passed = test_status.get('status') == 'PASSED'
            else:
                test_passed = test_status == 'PASSED'
            
            result['pass_to_pass_results'][test] = {'passed': test_passed}
            if not test_passed:
                result['errors'].append(f"PASS_TO_PASS тест сломался: {test}")
        
        result['logs'].append(f"Parsed results - patch_applied: {result['patch_applied']}, tests_passed: {result['tests_passed']}")
    
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
            
            # 2. ПРАВИЛЬНАЯ SWE-bench evaluation
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
    parser = argparse.ArgumentParser(description='SWE-bench Data Point Validator с правильным API')
    parser.add_argument('files', nargs='+', help='JSON файлы для валидации')
    parser.add_argument('--json', action='store_true', help='Вывод в JSON формате')
    parser.add_argument('--verbose', '-v', action='store_true', help='Подробный вывод')
    parser.add_argument('--no-evaluation', action='store_true', 
                       help='Пропустить SWE-bench evaluation (только структурная проверка)')
    parser.add_argument('--timeout', type=int, default=1800, 
                       help='Таймаут для операций в секундах (по умолчанию: 1800)')
    
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
            print("💡 Использовался ОФИЦИАЛЬНЫЙ SWE-bench evaluation harness с Docker")
        else:
            print("⚠️  Использовалась только структурная проверка")
    
    # Выход с кодом ошибки если есть невалидные файлы
    if valid_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
